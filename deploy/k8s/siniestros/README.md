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
puede borrar versiones. `deployment.yaml` recibe `${SINIESTROS_EVIDENCIAS_BUCKET}`:
Terraform pasa el bucket real a CodeBuild y `deploy.sh` exige esa variable antes
de invocar AWS o Kubernetes. El nombre no queda anclado a otra cuenta.

## Trabajadores parametricos (HU30 y HU31)

Tras confirmar el rollout del API, `deploy/codebuild/deploy.sh` aplica
`workers-pdb.yaml` y `workers.yaml` con la misma imagen y espera su rollout; si un
trabajador falla, revierte ese Deployment y el pipeline termina en error:

- `siniestros-eventos-parametricos`: `python -m siniestros.parametric_worker --limit 5000`.
- `siniestros-pagos-parametricos`: `python -m siniestros.payment_worker --limit 100`.

Cada uno corre con 2 replicas repartidas por nodo y un PodDisruptionBudget con
`minAvailable: 1`. Es seguro porque la cola usa `FOR UPDATE SKIP LOCKED` y cada pago
se reserva con un lease de 30 s: si una replica muere a mitad de un pago, otra marca
el intento huerfano como `ambiguo` (`lease_vencido`), consulta al proveedor y no
repite el efecto. Los trabajadores no usan el bucket ni la cuenta IRSA, y solo leen
`INTERNAL_API_TOKEN` y `SINIESTROS_DATABASE_URL` de `siniestros-runtime`.

No se configura `FUENTES_PARAMETRICAS_JSON`: sin fuentes, la recepcion de eventos
parametricos responde 401 a cualquier solicitud y los trabajadores quedan en espera.
La prueba de capacidad de 1.000.000 de eventos en 10 minutos y la medicion de
disponibilidad siguen pendientes de ejecutarse en AWS (ver el README del servicio).

Verificacion:

```bash
kubectl -n siniestros get deploy,pdb -l app.kubernetes.io/part-of=siniestros
kubectl -n siniestros get pdb siniestros-eventos-parametricos siniestros-pagos-parametricos
```

## Prerrequisitos de la primera entrega

1. Aplicar Terraform desde la rama integrada, incluido el tercer nodo (ver
   `terraform/environments/prod/primary/README.md`). Comprobar el bucket:
   `terraform output -raw siniestros_evidence_bucket` debe coincidir con el valor
   de `SINIESTROS_EVIDENCIAS_BUCKET` del proyecto CodeBuild de despliegue.
   Para renderizado manual autorizado, exportar ese output junto con `AWS_REGION`
   e `IMAGE`, sin sustituir el placeholder por un bucket de otra cuenta.
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

5. Iniciar el pipeline a mano, fijando el commit integrado. Los disparadores por tag
   estan pausados (ver [Pausa temporal de CodePipeline](../../../terraform/environments/prod/primary/README.md#pausa-temporal-de-codepipeline))
   y la configuracion nueva exige aprobacion manual antes de Deploy. Revisar la
   ejecucion y aprobarla solo con prerrequisitos listos. Seguir build, migracion y
   rollout. El pipeline aplica `service.yaml` y `deployment.yaml` y soporta el primer
   Deployment inexistente.

```bash
aws codepipeline start-pipeline-execution \
  --name solventa-prod-siniestros --profile "$AWS_PROFILE" --region us-east-2 \
  --source-revisions actionName=Source,revisionType=COMMIT_ID,revisionValue=<sha-completo>
```

Al crearse, el pipeline puede lanzar una ejecucion sobre `main`. Mientras `main` no
incluya este despliegue, esa ejecucion falla sin cambiar el cluster; no reintentarla.

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
