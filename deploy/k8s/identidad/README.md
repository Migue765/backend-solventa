# Manifiestos Kubernetes - identidad

Servicio del contexto *Identidad y Cliente*: dueno de los usuarios, verifica credenciales y emite los JWT (RS256).
Los BFF le delegan la autenticacion por el DNS interno `http://identidad.identidad.svc.cluster.local:8080`.
Hacia internet solo se publica `/.well-known/openid-configuration` y `/.well-known/jwks.json` (a traves del API
Gateway); el authorizer JWT del gateway los descarga para validar los tokens.

Mismo esquema que `deploy/k8s` (cotizacion): los manifiestos con variables `${...}` se sustituyen con `envsubst` a
partir de los outputs de Terraform.

| Variable | Origen |
|---|---|
| `IRSA_ROLE_ARN` | `terraform output -raw identidad_role_arn` |
| `TARGET_GROUP_ARN` | `terraform output -raw identidad_target_group_arn` |
| `IMAGE` | `$(terraform output -raw identidad_ecr_repository_url):<tag>` |
| `JWT_ISSUER` | `terraform output -raw jwt_issuer` (URL base del API, sin barra final) |
| `JWT_AUDIENCE` | `terraform output -raw jwt_audience` (`solventa-api`) |
| `AWS_REGION` | `us-east-2` |

Parametros fijos del Deployment: `ACCESS_TOKEN_MINUTES=60`, `REFRESH_TOKEN_HOURS_WEB=8`,
`REFRESH_TOKEN_DAYS_MOVIL=30`, `DB_SCHEMA=identidad`.

## Orden de aplicacion

`identidad` se despliega **antes** que `bff-web` y `bff-movil`, que dependen de el.
Prerrequisitos: `terraform apply` de `terraform/environments/prod/primary` y `kubectl` apuntando al cluster
(`terraform output kubeconfig_command`).

```bash
cd terraform/environments/prod/primary
export IRSA_ROLE_ARN=$(terraform output -raw identidad_role_arn)
export TARGET_GROUP_ARN=$(terraform output -raw identidad_target_group_arn)
export IMAGE="$(terraform output -raw identidad_ecr_repository_url):<tag>"
export JWT_ISSUER=$(terraform output -raw jwt_issuer)
export JWT_AUDIENCE=$(terraform output -raw jwt_audience)
export AWS_REGION=us-east-2
cd ../../../../deploy/k8s/identidad

# 1. Namespace
kubectl apply -f namespace.yaml

# 2. Secreto identidad-config (DATABASE_URL, REDIS_URL y JWT_PRIVATE_KEY; externo, no se versiona). Ver mas abajo.

# 3. ServiceAccount (anotacion IRSA)
envsubst < serviceaccount.yaml | kubectl apply -f -

# 3b. Migraciones (un Job por version; MIGRATION_ID sin puntos, ej. 0-1-0)
export MIGRATION_ID=0-1-0
envsubst < migrate-job.yaml | kubectl apply -f -
kubectl -n identidad wait --for=condition=complete job/identidad-migrate-$MIGRATION_ID --timeout=240s

# 4. Servicio y deployment
kubectl apply -f service.yaml
envsubst < deployment.yaml | kubectl apply -f -

# 5. Autoscaling y disponibilidad
kubectl apply -f hpa.yaml -f pdb.yaml

# 6. Enlace al ALB interno (requiere el controller y el Service existente)
envsubst < targetgroupbinding.yaml | kubectl apply -f -
```

Verificacion:

```bash
kubectl -n identidad get pods,hpa,targetgroupbinding
API=$(terraform output -raw jwt_issuer)   # desde terraform/environments/prod/primary
curl -s "$API/.well-known/openid-configuration"   # "issuer" debe ser exactamente $API
curl -s "$API/.well-known/jwks.json"
```

Si el discovery no responde o su `issuer` no coincide con `$JWT_ISSUER`, el authorizer del gateway rechaza con 401
**todas** las rutas protegidas (`/v1/*` y `/movil/*` que no sean login, renovacion o registro).

## Esquema de base de datos

`DB_SCHEMA=identidad`: las tablas viven en un esquema propio de `solventa_prod` (un esquema aislado por dominio).
La migracion debe crearlo (`CREATE SCHEMA IF NOT EXISTS identidad`). Si no lo hace, crearlo una vez a mano con las
credenciales master antes del primer Job de migracion.

## Secreto `identidad-config`

Lee tres valores de Secrets Manager (el rol IRSA del servicio ya puede leerlos):

- Base: `terraform output -raw db_secret_arn` (credenciales master generadas por RDS).
- Redis: `terraform output -raw redis_secret_arn` (JSON con `auth_token`, `host`, `port` y `url`; `url` ya es
  `rediss://:<token>@<endpoint>:6379/0`). Se usa para el limitador de intentos de login.
- Clave JWT: `terraform output -raw jwt_key_secret_arn` (clave privada RSA en formato PEM).

```bash
cd terraform/environments/prod/primary
get() { aws secretsmanager get-secret-value --secret-id "$1" --query SecretString --output text \
  --profile tesis --region us-east-2; }
DB_SECRET=$(get "$(terraform output -raw db_secret_arn)")
DB_USER=$(echo "$DB_SECRET" | jq -r .username)
DB_PASS=$(echo "$DB_SECRET" | jq -r .password | jq -sRr @uri)
REDIS_URL=$(get "$(terraform output -raw redis_secret_arn)" | jq -r .url)

kubectl -n identidad create secret generic identidad-config \
  --from-literal=DATABASE_URL="postgresql://${DB_USER}:${DB_PASS}@$(terraform output -raw db_endpoint):5432/solventa_prod" \
  --from-literal=REDIS_URL="${REDIS_URL}" \
  --from-file=JWT_PRIVATE_KEY=<(get "$(terraform output -raw jwt_key_secret_arn)")
```

La contrasena de RDS rota y el Secret queda desactualizado: hay que volver a sincronizarlo (o usar External Secrets).
El auth token de Redis no rota solo.

### Rotacion de la clave JWT

La clave privada tambien esta en el state de Terraform (bucket S3 cifrado), ademas de en Secrets Manager. Para rotarla:

```bash
cd terraform/environments/prod/primary
terraform apply -replace=module.jwt_key.tls_private_key.this    # lo ejecuta una persona, no el pipeline
# recrear el Secret identidad-config (JWT_PRIVATE_KEY) como arriba y reiniciar:
kubectl -n identidad rollout restart deployment/identidad
```

No hay periodo de solapamiento: los access tokens firmados con la clave anterior dejan de validar (duran como
maximo 60 minutos) y los clientes deben renovar. Conviene rotar fuera de horas de uso.

## Notas de operacion

### SOL-326: consentimiento y Outbox

No desplegar esta version hasta contar con un cliente compatible con la solicitud
completa de consentimiento (coordinacion con SOL203). Una aprobacion booleana sola
ahora devuelve 400; los registros antiguos sin evidencia completa dejan de autorizar
perfilamiento. No ampliar permisos durante una migracion.

Orden de rollout en una ventana coordinada, sin trafico de cotizacion durante la
mezcla de versiones:

1. Conservar digests y respaldo de las bases. Preparar el cliente compatible y las
   imagenes de BFF, Identidad y Perfilamiento. Confirmar el secreto interno compartido
   y la base existente de Perfilamiento; no crear otra base ni un servicio demo.
2. Crear `eventos_consentimiento` ejecutando el Job de Perfilamiento con la imagen
   nueva (`SERVICE_NAME=perfilamiento`, `NAMESPACE=perfilamiento`, `MIGRATE=true`).
   `init-db` usa `create_all`: conserva tablas y datos existentes. Desplegar su consumidor
   y PEP. El PEP nuevo deniega evidencia legada; no habilitar trafico todavia.
3. Desplegar Identidad con `MIGRATE=true`: Alembic `0003` agrega intentos, error y acuse
   al Outbox sin reescribir el historial. `deploy.sh` aplica el CronJob solo tras un
   rollout exitoso de Identidad. Usa `identidad-config/DATABASE_URL`, esquema `identidad`,
   `profiling-internal/INTERNAL_API_TOKEN` y el Service interno de Perfilamiento en 8080.
4. Desplegar BFF Web y el cliente compatible. Conservar la integracion existente de
   Cotizacion con Perfilamiento y su flag habilitado. Reabrir trafico solo tras el
   recorrido autenticado de aprobacion, perfil simulado, cotizacion, ofertas y revocacion.
   Antes de ejecutar el worker comprobar que la revocacion durable ya deniega nuevas
   consultas; luego verificar evento y acuse, sin duplicar auditoria al reintentar.

El CronJob `identidad-consent-outbox` corre cada minuto, con `concurrencyPolicy=Forbid`,
deadline de 50 segundos y sin reintento automatico del Job. Cada tick procesa hasta
50 eventos y prioriza revocaciones/rechazos. Cinco fallos por evento lo dejan en DLQ
logica; investigar y ejecutar manualmente el modulo con `--reintentar-fallidos` usando
la misma imagen/configuracion. No borrar filas para ocultar errores.

Monitorear Jobs fallidos, antiguedad y volumen de pendientes, filas con `intentos>=5`
y la diferencia entre `acuse.aplicadaEn` y `contenido.efectivaEn` (objetivo <=300 s).
Requiere relojes sincronizados. Un backlog superior a la capacidad o la indisponibilidad
del consumidor puede exceder el objetivo; el CronJob y las pruebas locales no certifican
el SLO de produccion. Este cambio no instala alarmas, Redis/batch/analitica ni proveedores
Open Finance reales.

Rollback: suspender el CronJob y cerrar temporalmente el flujo de cotizacion antes de
revertir codigo incompatible. Conservar tablas, historial, Outbox, acuses y perfiles.
No hacer downgrade destructivo de `0003`, eliminar la tabla del consumidor ni volver a
un PEP que autorice evidencia legada mientras haya trafico. Corregir hacia delante
cuando no pueda preservarse la denegacion por defecto.

Esta entrega no ejecuta migraciones ni despliegues en el cluster. La aceptacion en
PostgreSQL/Kubernetes, carga y observabilidad corresponde al rollout autorizado.

### Operacion general

- Release: tag `identidad-v*` (disjunto de `v*` de cotizacion, `bff-web-v*` y `bff-movil-v*`); el pipeline migra la
  base y despliega. Imagen con tag inmutable (`<version>-<commit7>`).
- Imagen `linux/amd64`: en Apple Silicon usar `docker buildx build --platform linux/amd64`.
- CPU: el hash PBKDF2 (600.000 iteraciones) cuesta ~1 CPU por login; limite del pod 1000m y HPA al 60 % de CPU.
- `POST /v1/sesiones`, `POST /v1/sesiones/renovar` y `POST /v1/usuarios` (y sus equivalentes `/movil/v1/...`) tienen
  throttling de 5 req/s, burst 10, en el API Gateway. Es un limite global por ruta, no por cliente.
- Trafico BFF -> identidad: pod a pod por ClusterIP dentro del cluster; no requiere cambios en Security Groups
  (los pods comparten el SG primario del cluster, que permite el trafico entre nodos).
