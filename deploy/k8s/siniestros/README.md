# Despliegue de Siniestros

Servicio interno (`http://siniestros.siniestros.svc.cluster.local:8080`), sin ruta
publica ni TargetGroupBinding: lo llaman BFF Web, BFF Movil y Asistencias con
`INTERNAL_API_TOKEN`. `terraform/environments/prod/primary/siniestros.tf` crea ECR
`solventa/siniestros`, el pipeline `solventa-prod-siniestros` (tag `siniestros-v*`,
disjunto de los otros servicios), el bucket de evidencias y el rol IRSA. Revisar el
plan antes del apply; este documento no autoriza cambiar recursos ajenos ni ejecutar
un apply completo sin revision.

## Evidencias en S3

La evidencia confirmada se sube con el SDK de AWS al bucket privado
`solventa-prod-siniestros-evidencias-<cuenta>`, bajo `evidencias/`. El bucket no se
monta como disco. El pod solo usa un `emptyDir` de 2 GiB como staging de cargas en
curso y temporales de descarga: si el pod se reinicia, las cargas pendientes vuelven
a empezar, pero la evidencia confirmada ya esta en S3. Por eso se mantiene una
replica (ver [Persistencia y acceso](../../../services/siniestros/README.md#persistencia-y-acceso)).

El bucket cifra con la CMK del entorno, exige TLS, bloquea el acceso publico y tiene
versionado sin Object Lock: una version sobrescrita o borrada se recupera durante 7
dias y despues se elimina definitivamente. El rol IRSA solo permite `s3:PutObject`,
`s3:GetObject` y `s3:DeleteObject` sobre `evidencias/*`, mas el uso de la CMK; no
puede borrar versiones. `deployment.yaml` repite el nombre del bucket en
`SINIESTROS_EVIDENCIAS_BUCKET` y las pruebas de infraestructura lo comparan con
Terraform.

No se despliega el trabajador parametrico ni se configura `FUENTES_PARAMETRICAS_JSON`:
sin fuentes, la recepcion de eventos parametricos responde 401 a cualquier solicitud.

## Prerrequisitos de la primera entrega

1. Aplicar Terraform desde la rama integrada, incluido el tercer nodo (ver
   `terraform/environments/prod/primary/README.md`). Comprobar el bucket:
   `terraform output -raw siniestros_evidence_bucket` debe coincidir con
   `SINIESTROS_EVIDENCIAS_BUCKET` de `deployment.yaml`.
2. Crear una base PostgreSQL propia y un usuario con permisos solo sobre ella
   mediante el procedimiento de bases aprobado, sin imprimir credenciales ni
   versionarlas. No compartir tablas ni usar SQLite.
3. Aplicar `namespace.yaml` y sincronizar el Secret externo `siniestros-runtime` con
   `SINIESTROS_DATABASE_URL` (driver `postgresql+psycopg`) y el mismo
   `INTERNAL_API_TOKEN` de Polizas, copiado byte a byte y sin salto de linea final:
   Siniestros no recorta el valor. El pipeline no crea ni obtiene secretos.
4. Crear el ServiceAccount con el rol IRSA antes del primer despliegue. Sin la
   anotacion, `deploy/codebuild/deploy.sh` se detiene antes de migrar.

```bash
cd terraform/environments/prod/primary
export IRSA_ROLE_ARN=$(terraform output -raw siniestros_role_arn)
cd ../../../../deploy/k8s/siniestros
envsubst < serviceaccount.yaml | kubectl apply -f -
```

5. Publicar un tag `siniestros-v<version>` del release y seguir build, migracion y
   rollout. El pipeline aplica `service.yaml` y `deployment.yaml` y soporta el primer
   Deployment inexistente.

Verificacion: `kubectl -n siniestros get pods,svc` y, sin mostrar valores, que el pod
recibio la identidad IRSA:

```bash
kubectl -n siniestros get pod -l app.kubernetes.io/name=siniestros \
  -o jsonpath='{.items[0].spec.containers[0].env[*].name}'
```

La lista debe incluir `AWS_ROLE_ARN` y `AWS_WEB_IDENTITY_TOKEN_FILE`.

## Activacion en los BFF

El pipeline de BFF Web solo cambia la imagen y `PERFILAMIENTO_SERVICE_URL`. Comprobar
que su Deployment ya apunta a Siniestros y, si la salida esta vacia, activarlo:

```bash
kubectl -n bff-web get deployment bff-web \
  -o jsonpath='{.spec.template.spec.containers[?(@.name=="bff-web")].env[?(@.name=="SINIESTROS_SERVICE_URL")].value}'
kubectl -n bff-web set env deployment/bff-web SINIESTROS_SERVICE_URL=http://siniestros.siniestros.svc.cluster.local:8080
kubectl -n bff-web rollout status deployment/bff-web --timeout=300s
```

BFF Movil se activa junto con Notificaciones y Asistencias, segun su
[README](../bff-movil/README.md).

## Aceptacion de negocio obligatoria

Con un cliente de prueba controlado, desde la web y la app: registrar un siniestro,
consultar estado e historial, subir una foto y un documento, confirmar, listarlos y
descargarlos. Reiniciar el Deployment (`kubectl -n siniestros rollout restart
deployment/siniestros`) y volver a descargarlos: deben conservar contenido y SHA256.
Cancelar una carga pendiente libera su cupo. Un operador autorizado comprueba que los
objetos existen bajo `evidencias/`, sin descargar ni exponer su contenido. Otro
cliente no debe ver los siniestros ni las evidencias del primero. Ready, `/health` o
la prueba local con S3 simulado no sustituyen esta aceptacion.

## Rollback

Conservar la base y el bucket; no borrar objetos ni versiones para recuperar. Para
retirar la dependencia, desactivar primero los BFF:

```bash
kubectl -n bff-web set env deployment/bff-web SINIESTROS_SERVICE_URL-
kubectl -n bff-movil set env deployment/bff-movil SINIESTROS_SERVICE_URL-
```

Sin URL, los BFF responden 503 en las rutas de siniestros; no simulan listas vacias.
Un rollout fallido del pipeline ya revierte a la imagen anterior.
