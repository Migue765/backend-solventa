# Manifiestos Kubernetes - cotizacion

Los manifiestos `serviceaccount`, `deployment` y `targetgroupbinding` usan variables `${...}` que se sustituyen con
`envsubst` a partir de los outputs de Terraform.

| Variable | Origen |
|---|---|
| `IRSA_ROLE_ARN` | `terraform output -raw service_role_arn` |
| `TARGET_GROUP_ARN` | `terraform output -raw target_group_arn` |
| `IMAGE` | `$(terraform output -raw ecr_repository_url):<tag>` |
| `AWS_REGION` | `us-east-2` |

## Orden de aplicacion

Prerrequisito: `terraform apply` de `terraform/environments/prod/primary` (instala AWS Load Balancer Controller y metrics-server)
y `kubectl` apuntando al cluster (`terraform output kubeconfig_command`).

```bash
cd terraform/environments/prod/primary
export IRSA_ROLE_ARN=$(terraform output -raw service_role_arn)
export TARGET_GROUP_ARN=$(terraform output -raw target_group_arn)
export IMAGE="$(terraform output -raw ecr_repository_url):<tag>"
export AWS_REGION=us-east-2
cd ../../../../deploy/k8s

# 1. Namespace
kubectl apply -f namespace.yaml

# 2. Secreto DATABASE_URL (externo: no se versiona). Ver nota abajo.

# 3. ServiceAccount (anotacion IRSA)
envsubst < serviceaccount.yaml | kubectl apply -f -

# 3b. Migraciones de base de datos (un Job por version; MIGRATION_ID sin puntos, ej. 0-1-0)
export MIGRATION_ID=0-1-0
envsubst < migrate-job.yaml | kubectl apply -f -
kubectl -n cotizacion wait --for=condition=complete job/cotizacion-migrate-$MIGRATION_ID --timeout=240s

# 4. Servicio y deployment
kubectl apply -f service.yaml
envsubst < deployment.yaml | kubectl apply -f -

# 5. Autoscaling y disponibilidad
kubectl apply -f hpa.yaml -f pdb.yaml

# 6. Enlace al ALB interno (requiere el controller y el Service existente)
envsubst < targetgroupbinding.yaml | kubectl apply -f -
```

Verificacion: `kubectl -n cotizacion get pods,hpa,targetgroupbinding` y `curl "$(terraform output -raw api_endpoint)/ping"`.

## Secreto `cotizacion-db` (DATABASE_URL)

El deployment lee `DATABASE_URL` del Secret `cotizacion-db`. Las credenciales viven en Secrets Manager
(`terraform output db_secret_arn`, generado por RDS). Opciones:

- Recomendada: External Secrets Operator o Secrets Store CSI Driver sincronizando el secreto de Secrets Manager
  (el rol IRSA del servicio ya puede leerlo). No se instala por Terraform en dev.
- Manual (dev), con una identidad que tenga acceso al secreto:

```bash
SECRET=$(aws secretsmanager get-secret-value --secret-id "$(terraform output -raw db_secret_arn)" \
  --query SecretString --output text --profile tesis --region us-east-2)
DB_USER=$(echo "$SECRET" | jq -r .username)
DB_PASS=$(echo "$SECRET" | jq -r .password | jq -sRr @uri)
kubectl -n cotizacion create secret generic cotizacion-db \
  --from-literal=DATABASE_URL="postgresql://${DB_USER}:${DB_PASS}@$(terraform output -raw db_endpoint):5432/solventa_prod"
```

Si la contrasena cambia (rotacion), hay que volver a sincronizar el Secret.

## Notas de operacion

- Las imagenes se publican con tag inmutable (la version, ej. `0.1.0`); no existe `latest`.
- La imagen es `linux/amd64` (nodos x86): en un Mac con Apple Silicon construir con
  `docker buildx build --platform linux/amd64`.
- `GET /ping` a traves de API Gateway lo responde el propio gateway (`Healthy Connection`) y no llega a
  los pods; probar con un endpoint real (`POST /v1/cotizaciones`). El health check del ALB si usa `/ping`
  del pod.
- Las credenciales de la base se copiaron al Secret `cotizacion-db`. RDS rota el secreto gestionado, asi
  que ese Secret quedara desactualizado: pendiente leerlo en runtime con el rol IRSA o usar External Secrets.

## Consolidacion y retirada acotada de duplicados

Este procedimiento no ejecuta la retirada: requiere validacion del flujo principal,
aceptacion de sus resultados y orden final expresa del coordinador. Un pod Ready o
`/ping` no bastan. Comprobar desde la aplicacion autenticada el recorrido de cotizacion,
seleccion, suscripcion, pago simulado y tablero con persistencia y aislamiento entre
clientes; verificar tambien consentimiento/perfilamiento cuando corresponda.
Los pagos simulados no acreditan emision de polizas.

Antes de retirar, registrar imagenes/digests, inventario, dependencias y resultados
del recorrido. Revisar pipelines y otros controladores externos al repositorio:
no debe quedar ningun automatismo o consumidor que recree o dependa de los duplicados.
El guard de `deploy/codebuild/deploy.sh` rechaza `hu11-perfilamiento` y
`calcular-cotizacion-preview` antes de llamar a AWS o Kubernetes, pero no es una
politica de admision ni reemplaza esa comprobacion.

### Los cuatro workloads HU11

Seguir el [runbook de perfilamiento](perfilamiento/README.md) y usar el helper
existente, desde la raiz del repositorio, solo despues de la aceptacion final:

```powershell
python scripts\migrate_perfilamiento.py retire --acceptance-confirmed
```

Su alcance sigue siendo exclusivamente los Deployments `bff-web`, `cotizacion`,
`identidad` y `perfilamiento` del namespace `hu11-perfilamiento`, con sus comprobaciones
de destino productivo, referencias y controladores. No ampliar el helper de cuatro
a cinco workloads ni incluir el preview en esta operacion.

### Preview: un unico Deployment, en una operacion separada

La allowlist exacta es `deployment/calcular-cotizacion` dentro del namespace
`calcular-cotizacion-preview`, no el namespace completo ni otros Deployments.
Su SQLite esta en `emptyDir` y **no esta vacio**: el inventario confirmado contiene
15 cotizaciones, 13 selecciones y 3 usuarios. Reiniciar, escalar a cero o eliminar
el pod/Deployment pierde ese almacenamiento; no usar esas acciones como ensayo.
Antes de cualquier retirada se necesita autorizacion de descarte o un backup
verificado y restaurable. El usuario ya autorizo descartar los datos efimeros
exclusivamente de este preview al retirarlo despues de validar; esa autorizacion
no permite borrar datos de otros entornos ni adelantar la orden final del coordinador.
No copiar ni migrar esos usuarios demo a produccion.

Revisar el inventario final antes de ejecutar. Si cambian el nombre, los controladores
o las dependencias esperadas, detenerse y revisar el alcance. Con la aceptacion y
orden final registradas, la unica eliminacion autorizada para este preview es:

```powershell
kubectl --context arn:aws:eks:us-east-2:976083331509:cluster/solventa-prod-eks -n calcular-cotizacion-preview delete deployment/calcular-cotizacion --wait=true
```

En ambas retiradas conservar namespaces, Secrets, Services, TargetGroupBindings,
rutas publicas/internas, PostgreSQL y capacidad del cluster. No ejecutar Terraform,
cambios de rutas, `delete namespace`, eliminaciones globales ni limpiezas adicionales.
Registrar el resultado y repetir el recorrido principal aceptado; verificar que los
Deployments retirados no reaparezcan. El helper y el guard no autorizan despliegues
desde una feature: integrar cambios mediante las PR y pipelines principales habituales.
