# Despliegue de Asistencias

Servicio interno (`http://asistencias.asistencias.svc.cluster.local:8080`), sin ruta
publica ni TargetGroupBinding: solo lo llama BFF Movil. Consulta Polizas y Siniestros
por sus URL internas, ya declaradas en `deployment.yaml`.
`terraform/environments/prod/primary/asistencias.tf` crea ECR `solventa/asistencias` y
el pipeline `solventa-prod-asistencias` (tag `asistencias-v*`). No usa rol IRSA ni
volumenes. Revisar el plan antes del apply; este documento no autoriza cambiar
recursos ajenos.

La migracion inicial carga tres prestadores sinteticos, dos en Bogota y uno en
Medellin, solo para pruebas. No representan empresas reales.

## Prerrequisitos de la primera entrega

1. Aplicar Terraform desde la rama integrada y desplegar antes Siniestros (ver su
   [README](../siniestros/README.md)); Polizas ya debe estar desplegado.
2. Crear una base PostgreSQL propia y un usuario con permisos solo sobre ella
   mediante el procedimiento de bases aprobado, sin imprimir credenciales ni
   versionarlas.
3. Aplicar `namespace.yaml` y sincronizar el Secret externo `asistencias-runtime`
   con `ASISTENCIAS_DATABASE_URL` (driver `postgresql+psycopg`) y el mismo
   `INTERNAL_API_TOKEN` de Polizas, copiado byte a byte y sin salto de linea final:
   Asistencias no recorta el valor. El pipeline no crea ni obtiene secretos.
4. Iniciar el pipeline a mano, fijando el commit integrado. Los disparadores por tag
   estan pausados (ver [Pausa temporal de CodePipeline](../../../terraform/environments/prod/primary/README.md#pausa-temporal-de-codepipeline))
   y no hay aprobacion manual: la ejecucion despliega. Seguir build, migracion y
   rollout. El pipeline aplica `service.yaml` y `deployment.yaml`.

```bash
aws codepipeline start-pipeline-execution \
  --name solventa-prod-asistencias --region us-east-2 \
  --source-revisions actionName=Source,revisionType=COMMIT_ID,revisionValue=<sha-completo>
```

Al crearse, el pipeline puede lanzar una ejecucion sobre `main`. Mientras `main` no
incluya este despliegue, esa ejecucion falla sin cambiar el cluster; no reintentarla.

Verificacion: `kubectl -n asistencias get pods,svc`.

## Activacion

BFF Movil se activa junto con Siniestros y Notificaciones, segun su
[README](../bff-movil/README.md).

## Aceptacion de negocio obligatoria

Con un cliente de prueba controlado desde la app y una ubicacion manual en Bogota o
Medellin: consultar prestadores cercanos y registrar una asistencia con una poliza
vigente del cliente y otra con un siniestro suyo. Repetir el registro con la misma
clave debe devolver la misma asistencia. Una poliza o un siniestro de otro cliente
debe responder 404. Ready, `/health` o una prueba local no sustituyen esta
aceptacion.

## Rollback

Conservar la base. Para retirar la dependencia:

```bash
kubectl -n bff-movil set env deployment/bff-movil ASISTENCIAS_SERVICE_URL-
```

Sin URL, BFF Movil responde 503 en las rutas de asistencias y no simula un registro.
