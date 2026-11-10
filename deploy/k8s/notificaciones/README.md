# Despliegue de Notificaciones

Servicio interno (`http://notificaciones.notificaciones.svc.cluster.local:8080`), sin
ruta publica ni TargetGroupBinding. Lo llaman BFF Movil (bandeja y preferencias) y,
si se activa, Cotizacion (pagos confirmados). `terraform/environments/prod/primary/notificaciones.tf`
crea ECR `solventa/notificaciones` y el pipeline `solventa-prod-notificaciones` (tag
`notificaciones-v*`). No usa rol IRSA ni volumenes: todo el estado vive en su base.
Revisar el plan antes del apply; este documento no autoriza cambiar recursos ajenos.

## Programador de despachos

`despachos-cronjob.yaml` llama a `POST /v1/despachos` cada 5 minutos con el
`INTERNAL_API_TOKEN` del Secret (`concurrencyPolicy: Forbid`, 120 s como maximo).
Cada ejecucion procesa hasta 100 avisos vencidos: vencimientos, renovaciones y
reintentos. `deploy/codebuild/deploy.sh` lo aplica con la misma imagen y solo despues
de un rollout correcto. Solo se entrega el canal `in_app`; `email` y `push` quedan
como `proveedor_no_configurado` hasta que exista un adaptador.

## Prerrequisitos de la primera entrega

1. Aplicar Terraform desde la rama integrada (ver
   `terraform/environments/prod/primary/README.md`).
2. Crear una base PostgreSQL propia y un usuario con permisos solo sobre ella
   mediante el procedimiento de bases aprobado, sin imprimir credenciales ni
   versionarlas.
3. Aplicar `namespace.yaml` y sincronizar el Secret externo `notificaciones-runtime`
   con `NOTIFICACIONES_DATABASE_URL` (driver `postgresql+psycopg`) y el mismo
   `INTERNAL_API_TOKEN` de Polizas, copiado byte a byte. El pipeline no crea ni
   obtiene secretos.
4. Iniciar el pipeline a mano, fijando el commit integrado. Los disparadores por tag
   estan pausados (ver [Pausa temporal de CodePipeline](../../../terraform/environments/prod/primary/README.md#pausa-temporal-de-codepipeline))
   y no hay aprobacion manual: la ejecucion despliega. Seguir build, migracion y
   rollout. El pipeline aplica `service.yaml`, `deployment.yaml` y el CronJob.

```bash
aws codepipeline start-pipeline-execution \
  --name solventa-prod-notificaciones --region us-east-2 \
  --source-revisions actionName=Source,revisionType=COMMIT_ID,revisionValue=<sha-completo>
```

Al crearse, el pipeline puede lanzar una ejecucion sobre `main`. Mientras `main` no
incluya este despliegue, esa ejecucion falla sin cambiar el cluster; no reintentarla.

Verificacion: `kubectl -n notificaciones get pods,svc,cronjob`. Despues de 5 minutos,
`kubectl -n notificaciones get jobs` debe mostrar una ejecucion completada de
`notificaciones-despachos`; su log solo contiene el codigo HTTP y `entregasConfirmadas`.

## Activacion

BFF Movil se activa junto con Siniestros y Asistencias, segun su
[README](../bff-movil/README.md).

La publicacion desde Cotizacion es opcional. Al activarla, el conciliador tambien
publica, uno por ciclo, los pagos confirmados anteriores que nunca se publicaron:
los clientes recibiran avisos de pagos historicos. Decidirlo antes de activar:

```bash
kubectl -n cotizacion set env deployment/cotizacion NOTIFICACIONES_SERVICE_URL=http://notificaciones.notificaciones.svc.cluster.local:8080
kubectl -n cotizacion rollout status deployment/cotizacion --timeout=300s
```

## Aceptacion de negocio obligatoria

Con un cliente de prueba controlado desde la app: consultar y cambiar preferencias,
y comprobar que otro cliente no ve sus avisos. Si Cotizacion esta activada, un pago
simulado confirmado debe aparecer una sola vez en la bandeja `in_app`, aunque se
repita el pago o se recargue la pantalla. Ready, `/health` o una prueba local no
sustituyen esta aceptacion.

## Rollback

Conservar la base. Desactivar primero los productores y consumidores:

```bash
kubectl -n cotizacion set env deployment/cotizacion NOTIFICACIONES_SERVICE_URL-
kubectl -n bff-movil set env deployment/bff-movil NOTIFICACIONES_SERVICE_URL-
kubectl -n notificaciones patch cronjob notificaciones-despachos -p '{"spec":{"suspend":true}}'
```

Cotizacion conserva los eventos no publicados y los reintenta al reactivar la URL.
