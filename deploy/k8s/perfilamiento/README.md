# Perfilamiento productivo y retirada de HU11

`perfilamiento` tiene namespace, Deployment y Service propios. Cotizacion usa
`http://perfilamiento.perfilamiento.svc.cluster.local:8080`. No cambia el JWT,
las rutas publicas, las reglas de perfilamiento ni la base PostgreSQL existente.
Las fuentes sinteticas preexistentes no se modifican en esta migracion.

## Preparar sin cambiar trafico

Desde la raiz del repositorio, con `AWS_PROFILE=tesis` y permisos del cluster
`solventa-prod-eks` en `us-east-2`:

```powershell
python scripts\migrate_perfilamiento.py stage
```

El script fija explicitamente el contexto productivo. Obtiene el digest de la
imagen saludable HU11, comprueba su configuracion y crea namespace, Secret,
Deployment (una replica) y Service `perfilamiento`. Espera rollout y endpoints
listos. Conserva `SERVICE_APP` y la URL de Identidad productiva.

No hay ExternalSecret/CSI ni IRSA en el origen actual. Se copian exclusivamente
`INTERNAL_API_TOKEN` y `PERFILAMIENTO_DATABASE_URL` desde `hu11-runtime` a
`perfilamiento-runtime`. Los valores solo pasan en memoria entre las APIs de
Kubernetes; no se imprimen ni escriben en archivos. Si existe un secreto destino
diferente, IRSA, volumen o configuracion desconocida, se aborta para revisar.
No se borra el secreto original ni se ejecuta `init-db`: ambas instancias usan
exactamente el mismo backing store y esquema. No copiar las otras claves de HU11.

El staging no cambia Cotizacion ni retira HU11. Antes de continuar, comprobar
`/health`, conectividad PostgreSQL en lectura, tablas existentes, autenticacion
interna y acceso a Identidad; health por si solo no acredita acceso a la base.
No crear usuarios ni perfiles para esta verificacion preliminar.

## Cambio de trafico y aceptacion

Esperar confirmacion del coordinador de que los despliegues nuevos de
Cotizacion y BFF estan estables. Registrar sus imagenes/digests y revisar que
ningun consumidor externo dependa de los cuatro workloads HU11.

Solo entonces:

```powershell
kubectl --context arn:aws:eks:us-east-2:976083331509:cluster/solventa-prod-eks -n perfilamiento rollout status deployment/perfilamiento --timeout=300s
kubectl --context arn:aws:eks:us-east-2:976083331509:cluster/solventa-prod-eks -n cotizacion set env deployment/cotizacion PERFILAMIENTO_SERVICE_URL=http://perfilamiento.perfilamiento.svc.cluster.local:8080
kubectl --context arn:aws:eks:us-east-2:976083331509:cluster/solventa-prod-eks -n cotizacion rollout status deployment/cotizacion --timeout=300s
```

Coordinar una unica ejecucion del smoke autorizado de consentimiento,
cotizacion, recuperacion de perfil y pago (incluyendo ownership y replay).
Respetar el presupuesto global de cuentas/operaciones aprobado; este script
no crea cuentas ni pagos. Mantener los cuatro deployments HU11 saludables
hasta que el coordinador acepte los resultados.

## Rollback antes de aceptar

```powershell
kubectl --context arn:aws:eks:us-east-2:976083331509:cluster/solventa-prod-eks -n hu11-perfilamiento rollout status deployment/perfilamiento --timeout=300s
kubectl --context arn:aws:eks:us-east-2:976083331509:cluster/solventa-prod-eks -n cotizacion set env deployment/cotizacion PERFILAMIENTO_SERVICE_URL=http://perfilamiento.hu11-perfilamiento.svc.cluster.local:8080
kubectl --context arn:aws:eks:us-east-2:976083331509:cluster/solventa-prod-eks -n cotizacion rollout status deployment/cotizacion --timeout=300s
```

No usar `rollout undo` de Cotizacion: podria revertir cambios concurrentes de
pagos. Cambiar solo la URL. El nuevo servicio puede mantenerse para diagnostico.
Despues de la retirada, el origen ya no estara disponible para este rollback;
una recuperacion requerira restauracion explicita aprobada de su Deployment.

## Retirada acotada, despues de aceptar

```powershell
python scripts\migrate_perfilamiento.py retire --acceptance-confirmed
```

La bandera registra la decision humana/coordinada, no sustituye el smoke.
El script comprueba rollout/endpoints nuevos, URL productiva, estabilidad
Cotizacion/BFF, ausencia de referencias HU11 en otros controladores, ausencia
de HPA y de controladores HU11 adicionales. Elimina exclusivamente Deployments
`bff-web`, `cotizacion`, `identidad` y `perfilamiento` de `hu11-perfilamiento`;
espera la desaparicion de sus pods. Conservar namespace, Services, Secrets,
DB, PVC y datos. No tocar preview ni otros workloads, rutas o capacidad.
Registrar inventario antes/despues y comprobar de nuevo que no reaparezcan.

Los manifiestos ya no apuntan a HU11 y `deploy/codebuild/deploy.sh` rechaza ese
namespace antes de cualquier operacion AWS/Kubernetes. Terraform define un pipeline
y repositorio ECR propios para Perfilamiento, con tag `perfilamiento-v*`, namespace
`perfilamiento` y migraciones habilitadas. Los recursos solo existen tras una
aplicacion Terraform aprobada; los disparadores automaticos heredan la politica
global y permanecen pausados por defecto. El pipeline usa el build/deploy existente
con `SERVICE_NAME=perfilamiento`, `NAMESPACE=perfilamiento`, `MIGRATE=true` para SOL-326.
El nuevo `migrate-job.yaml` ejecuta `init-db` sobre la base existente y agrega la
tabla de eventos sin borrar perfiles ni auditoria. No reactivar manifiestos demo externos.
El guard no es una politica de admision: revisar tambien automatizaciones
fuera de este repositorio antes de autorizar la retirada.

Para SOL-326 seguir el [orden coordinado](../identidad/README.md#sol-326-consentimiento-y-outbox):
crear la tabla y desplegar el consumidor antes del Outbox de Identidad. Esta inicializacion
aditiva no cambia el procedimiento historico `stage/retire` de retirada HU11 descrito arriba.

Publicar esta hotfix hacia `main` con revision/checks requeridos, y propagar a
`develop` mediante el backport habitual. No aplicar Terraform global: su drift
ajeno a esta migracion no esta autorizado.

## SOL-201

El rollout es coordinado y no crea servicios alternativos ni cambia los secretos:

1. Publicar Cotizacion con el consumidor `estadoFuentes` opcional y nombres de fuente
   extensibles; todavia acepta los perfiles anteriores. Alinear el guard del frontend
   para aceptar fallback explicito vigente; conservar todas las validaciones de consentimiento.
2. Con la imagen nueva de Perfilamiento, ejecutar `migrate-job.yaml` contra
   `perfilamiento-runtime/PERFILAMIENTO_DATABASE_URL`. El comando exacto del Job es
   `python -m flask --app perfilamiento.app init-db`; el pipeline existente lo ejecuta con
   `SERVICE_NAME=perfilamiento`, `NAMESPACE=perfilamiento`, `MIGRATE=true`.
   Esperar Job completado antes de publicar el Deployment. Crea `cache_fuentes` y
   `versiones_perfilamiento`, ademas de las tablas existentes si faltan. No altera columnas
   ni borra perfiles/eventos/auditoria. Los contadores se inicializan desde el historial al
   primer guardado de cada cliente.
3. Publicar la misma imagen de Perfilamiento en su Deployment existente. Mantener
   `COTIZACION_PERFILAMIENTO_HABILITADO=true` y la URL productiva actual de Cotizacion.
   No hay variables nuevas obligatorias; los limites estan en `PoliticaConsultas`.
4. Tras el rollout SOL-203/SOL-326, comprobar desde la aplicacion consentimiento nuevo
   explicito, cotizacion, GET de perfil y persistencia; negar consentimiento revocado,
   vencido o legado. Correlacionar auditoria de cada dependencia con HTTP extremo a extremo.
   Pruebas locales cubren fallos, retries, breaker y timeout con una fuente que tarda 800 ms;
   no inyectar fallas globales en produccion para repetirlas.

El cambio no ejecuta ni reconfigura el Outbox. Conserva los pasos de Identidad0003/0004 y
su consumidor establecidos por el operador. La aprobacion historica sin evidencia completa
no habilita cache; requiere nueva decision explicita.

Para revertir, conservar las tablas aditivas. No volver a un consumidor Cotizacion que
rechace `estadoFuentes` mientras se lean perfiles con ese campo. Mantener el consumidor
compatible y decidir una imagen de Perfilamiento correctiva con el operador; no ejecutar
`rollout undo` ciego ni borrar evidencias para hacer pasar una imagen antigua.
## Publicacion acotada de HU44

La instancia `cicd_perfilamiento` activa `restrict_service_access=true`,
`artifact_force_destroy=false` y aprobacion manual; su ECR usa
`force_delete=false`. Los valores por defecto del modulo conservan el
comportamiento de los otros servicios. Revisar el plan guardado contra el state
productivo existente: aceptar solo creaciones dentro de `ecr_perfilamiento` y
`cicd_perfilamiento`, sin reemplazos, borrados ni cambios a otros recursos.
Conservar los disparadores pausados, la conexion existente y la regla ALB 11.

Los roles de build y deploy confian en CodeBuild solo desde la cuenta propia y
el ARN exacto de su proyecto. El rol de CodePipeline mantiene el trust de
servicio documentado: no agregar condiciones SourceArn/SourceAccount sin
soporte confirmado. Sus permisos alcanzan solo su bucket, sus dos proyectos y
la conexion existente con `FullRepositoryId` exacto. `BranchName` usa
`StringEqualsIfExists`: AWS no exige contexto de rama en todas las lecturas
por commit. Esto rechaza otra rama cuando AWS la informa, pero no acredita
pertenencia de un SHA a main. Verificar esa pertenencia y la procedencia antes
de aprobar Deploy.

La salida del SG nuevo permite TCP 443 para AWS/EKS, S3 y kubectl, y TCP 80
para los repositorios apt del fallback `gettext-base` del buildspec existente.
Los destinos publicos requieren `0.0.0.0/0`; no abrir otros puertos ni entrada
al build. El DNS de AmazonProvidedDNS no depende de una regla UDP 53 del SG.
Solo agregar entrada 443 al SG del cluster desde este SG nuevo.

EC2 necesita el comodin `network-interface/*` porque asigna el ID al crear la
interfaz. La creacion exige ademas el SG nuevo y las subnets privadas exactas.
DeleteNetworkInterface no ofrece una condicion de SG: se limita a la cuenta,
region, VPC y subnets autorizadas; conserva acceso residual a otras ENI de esas
subnets. No atribuir aislamiento por proyecto a ese permiso. La concesion de
uso de ENI se limita ademas a `codebuild.amazonaws.com`. Solo las lecturas
Describe de EC2 y GetAuthorizationToken de ECR necesitan `Resource="*"`.
Ninguno de los tres roles recibe `iam:PassRole`; el actor de provisionamiento
necesita pasarlos a sus respectivos servicios sin ampliar roles existentes.
S3 conserva logs/artefactos 30 dias y ECR mantiene su politica de diez imagenes.

La creacion del pipeline puede iniciar Source/Build aunque DetectChanges sea
false y no haya triggers. La aprobacion posterior al build solo retiene Deploy.
Para esta entrega, autorizar exclusivamente
`70b1d43c35a690965d06875929e8b2547da5f071`, sin PR51. Contrastar revision de
Source, `resolvedSourceVersion`, `COMMIT_ID`, resultado de pruebas, tag y digest
ECR antes de aprobar. Si la inicial toma otro SHA, detenerla sin aprobar Deploy
y lanzar una ejecucion con `sourceRevisions` explicito. Si toma el SHA autorizado,
reutilizar su imagen y ejecucion: los tags ECR son inmutables. El tag esperado es
`perfilamiento-0.1.0-70b1d43`; no reconstruirlo si ya existe.

Tras el apply revisado, usar primero el rol propio para leer Deployment,
replicas, imagen/digest, env por nombre y esquema, sin imprimir secretos.
Comprobar la base existente y las tablas `perfiles`, `referencias_perfil`,
`consultas_perfilamiento` y `eventos_consentimiento`. Ante diferencias, detenerse.
El init-db autorizado agrega solo `consultas_informacion_cliente`; no crear
las tablas de PR51 ni ejecutar inicializaciones que amplien ese alcance.
Guardar snapshot privado del Deployment, revision, imagen y esquema antes de
migrar; no copiar secretos. Verificar la tabla y las rutas del productor antes
de configurar en BFF web
`PERFILAMIENTO_SERVICE_URL=http://perfilamiento.perfilamiento.svc.cluster.local:8080`.
Conservar los demas valores de env y `ENCRYPTED_LOGIN_ENABLED=true`.

Comprobar el digest del pod y los contratos autenticados HU44/HU46 con replay,
ownership y rechazos reales. Las fuentes sinteticas no acreditan integracion
con bancos. Ante un fallo, restaurar la imagen/configuracion del snapshot y la
URL previa del BFF con su via propia. Conservar la tabla aditiva y sus datos:
la reversion de aplicacion no autoriza DROP ni rollback de la base.
