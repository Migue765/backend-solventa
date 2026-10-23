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
namespace antes de cualquier operacion AWS/Kubernetes. No existe pipeline de
Perfilamiento en la infraestructura actual: futuros despliegues deben usar
`SERVICE_NAME=perfilamiento`, `NAMESPACE=perfilamiento`, `MIGRATE=true` para SOL-326.
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
