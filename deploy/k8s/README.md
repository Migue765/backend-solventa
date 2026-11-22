# Manifiestos Kubernetes - cotizacion

Para reconstruir una cuenta vacia seguir [ARRANQUE-VACIO.md](ARRANQUE-VACIO.md).
Los procedimientos historicos de retirada mas abajo no aplican a ese arranque.

## Perfil academico de bajo consumo

Las nueve APIs (`bff-movil`, `bff-web`, `cotizacion`, `identidad`, `perfilamiento`,
`polizas`, `siniestros`, `notificaciones` y `asistencias`) mantienen **una replica por
servicio**. Los cuatro HPA existentes tienen minimo y maximo en 1. El despliegue reaplica
ese limite antes de actualizar la imagen y fija una replica; Polizas, Siniestros,
Notificaciones y Asistencias aplican su Deployment con `replicas: 1`, incluso en su primer
despliegue. La espera del rollout comprueba el resultado despues de ajustar la escala.
`maxSurge: 1` permite un segundo pod transitorio durante un rollout, no una segunda
replica permanente. Los PDB existentes conservan `minAvailable: 1`: bloquean desalojos
voluntarios de la unica replica, pero no garantizan disponibilidad ante fallos.

El grupo EKS usa tres `t3.small` (minimo, deseado y maximo en 3). Cada nodo admite
11 pods y tiene 1466644 Ki de memoria asignable en el inventario observado; los tres
suman 33 pods. Dos nodos no dejan capacidad suficiente para las nueve APIs, DNS,
controladores, DaemonSets y Jobs de migracion o tareas programadas, ademas del margen
para rollouts.
No se reducen requests ni limites de memoria/CPU sin medir el consumo.
RDS `db.t4g.micro` de 20 GB single-AZ y Redis `cache.t4g.micro` de un nodo ya son
las clases minimas configuradas; se conservan datos, red, autenticacion y balanceador
del flujo principal.

Este perfil ahorra recursos, pero **no acredita alta disponibilidad ni ASR-ESC-01**.
Para experimentar con carga/HA hay que restaurar replicas y capacidad y medir el
recorrido real. Las actualizaciones del grupo EKS no deben retirar un nodo con pods
sin comprobar antes su persistencia, capacidad de reubicacion y disponibilidad.

Las regresiones locales de manifiestos y despliegue se ejecutan con
`python -m pytest tests/test_academic_infrastructure.py`; las llamadas a AWS/Kubernetes
se sustituyen por dobles de prueba, sin modificar el cluster. No sustituyen la
validacion autenticada del flujo principal con persistencia.

### Servicios internos sin ruta publica

Siniestros, Notificaciones y Asistencias solo exponen un Service `ClusterIP`, sin HPA,
PDB ni TargetGroupBinding; los consumen los BFF y otros servicios. Sus runbooks estan
en [siniestros](siniestros/README.md), [notificaciones](notificaciones/README.md) y
[asistencias](asistencias/README.md). `deploy/codebuild/deploy.sh` exige que el
ServiceAccount de Siniestros tenga su rol IRSA antes de migrar y aplica el CronJob de
despachos de Notificaciones despues de un rollout correcto.

### Actualizacion acotada de BFF Web

`deploy/codebuild/deploy.sh` obtiene `PERFILAMIENTO_SERVICE_URL` del contenedor
`bff-web` en `deploy/k8s/bff-web/deployment.yaml`. Renderiza localmente con `kubectl`
un patch estrategico que cambia **solo la imagen y esa variable**, en una unica
mutacion del pod template. No aplica el Deployment completo: conserva el resto del
entorno y sus referencias a Secrets, incluido `ENCRYPTED_LOGIN_ENABLED` activado
en runtime aunque el manifiesto declare `false`. Una URL ausente/vacia o un error
de renderizado detiene la actualizacion antes de modificar el Deployment.

Si no se confirma el patch, el script falla y pide revisar el estado antes de
reintentar; no revierte a ciegas una revision desconocida. Si falla el rollout,
el rollback existente revierte conjuntamente imagen y dependencia, no una
revision intermedia con solo uno de esos cambios. Tanto el fallo de rollback
como su confirmacion se informan explicitamente; un rollout fallido nunca
termina como despliegue exitoso. El comportamiento de imagen, migraciones y
capacidad de los demas servicios se conserva.

El harness usa AWS/Kubernetes simulados; solo el renderizado y el merge de patches
usan `kubectl --local`, sin acceder al cluster. Requiere `bash` y `kubectl` locales
para esas comprobaciones. Las pruebas conservan cifrado, variables ajenas,
referencias a Secrets y contenedores adicionales. No ejecutan un despliegue real.

Los manifiestos `serviceaccount`, `deployment` y `targetgroupbinding` usan variables `${...}` que se sustituyen con
`envsubst` a partir de los outputs de Terraform.

| Variable | Origen |
|---|---|
| `IRSA_ROLE_ARN` | `terraform output -raw service_role_arn` |
| `TARGET_GROUP_ARN` | `terraform output -raw target_group_arn` |
| `IMAGE` | `$(terraform output -raw ecr_repository_url):<tag>` |
| `AWS_REGION` | `us-east-2` |

## Migraciones en una base logica

Contrato pendiente de aprovisionamiento autorizado: una base `solventa_prod`,
siete esquemas con propietarios `<dominio>_owner` **NOLOGIN**, roles runtime sin
DDL ni pertenencia al owner, y logins `<dominio>_migrator` separados. Cada DSN
migrador conserva `options=-crole=<dominio>_owner` y su `search_path` exclusivo.
La sesion debe mostrar `session_user=<dominio>_migrator`,
`current_user=<dominio>_owner` y `current_schema()=<dominio>`.
No basta con que los DSN apunten a una sola instancia RDS.

Antes de ejecutar Jobs, entregar privadamente los siguientes Secrets en el
namespace de su dominio. Cada uno contiene **solo** la clave indicada; no
versionar ni imprimir el DSN, YAML renderizado o passwords.

| Secret | Clave DSN |
|---|---|
| `cotizacion-migrator` | `DATABASE_URL` |
| `identidad-migrator` | `DATABASE_URL` |
| `perfilamiento-migrator` | `PERFILAMIENTO_DATABASE_URL` |
| `polizas-migrator` | `POLIZAS_DATABASE_URL` |
| `siniestros-migrator` | `SINIESTROS_DATABASE_URL` |
| `notificaciones-migrator` | `NOTIFICACIONES_DATABASE_URL` |
| `asistencias-migrator` | `ASISTENCIAS_DATABASE_URL` |

Los Deployments y sus Secrets runtime no cambian. Perfilamiento conserva su
`INTERNAL_API_TOKEN` en `perfilamiento-runtime` e inicializa tablas nuevas mediante
`flask --app perfilamiento.app init-db`; no tiene historial Alembic.
Los seis Jobs Alembic declaran `DB_RUNTIME_ROLE=<dominio>_runtime`. Sus `env.py`
revocan al rol runtime todos los permisos sobre su propia tabla de version,
dentro de la transaccion online, tambien cuando `upgrade head` no tiene cambios.
Los errores abortan el Job; con esa variable no se admite SQLite.
Los nombres/historiales existentes no se fusionan ni reinician.
Las ejecuciones ad hoc deben declarar tambien `DB_RUNTIME_ROLE`; omitirlo
conserva el comportamiento legado, no acredita esta restriccion.

Identidad mantiene las opciones libpq del DSN y configura el `search_path` en
la conexion. Si el esquema ya existe, no intenta crearlo de nuevo con un rol
sin `CREATE` sobre la base. No se conceden permisos elevados para eludir errores.
No se cambian los contextos Docker: el postpaso se empaqueta con cada `env.py`.

Estos manifiestos no crean Secrets, roles, esquemas ni bases, ni instalan
External Secrets/CSI. Entrega, rotacion, aislamiento y estado previo de datos
siguen siendo gates de despliegue. No ejecutar el helper local contra RDS:
sus operaciones de cluster nuevo/`CREATE DATABASE` no son un procedimiento de
migracion de una base administrada existente.

Comprobacion sintetica local, una vez disponible el bootstrap PostgreSQL de PR129:

```powershell
$env:SOLVENTA_TEST_POSTGRES_BIN = $pgBin
.\.venv\Scripts\python.exe -m pytest tests\test_postgres_migration_jobs.py -q
```

Requiere los paquetes del bootstrap documentado en el README raiz y las
dependencias de desarrollo existentes (`requirements-dev.txt`); `$pgBin` apunta a
la distribucion PostgreSQL 17 verificada, no a una instalacion descargada por el
test. Ejecuta los comandos de los siete Jobs en procesos locales sobre dos
clusters sinteticos nuevos (Identidad con/sin `DB_SCHEMA`), repite migraciones
sin cambios y comprueba que reparan permisos del historial concedidos de nuevo
solo por el test. Valida 73 tablas de dominio (70 de los seis dominios web mas
3 de Asistencias), seis historiales, propietarios,
roles, CRUD y denegaciones cruzadas. No ejecuta Kubernetes ni servicios AWS;
sin binarios explicitos omite los casos PostgreSQL, sin sustituirlos por SQLite.

## Orden de aplicacion

Prerrequisito: infraestructura `terraform/environments/prod/primary` y despues
`terraform/environments/prod/addons` (AWS Load Balancer Controller y metrics-server),
con planes y ejecucion autorizados. Para una cuenta nueva seguir primero el
[procedimiento de reconstruccion](../../terraform/environments/prod/primary/README.md#preparacion-y-primer-despliegue):
state nuevo, bases/Secrets y Deployment semilla sin pods antes de migrar.
Apuntar `kubectl` al cluster aprobado (`terraform output kubeconfig_command`).

```bash
cd terraform/environments/prod/primary
export IRSA_ROLE_ARN=$(terraform output -raw service_role_arn)
export TARGET_GROUP_ARN=$(terraform output -raw target_group_arn)
export IMAGE="$(terraform output -raw ecr_repository_url):<tag>"
export AWS_REGION=us-east-2
cd ../../../../deploy/k8s

# 1. Namespace
kubectl apply -f namespace.yaml

# 2. Secrets runtime cotizacion-db y cotizacion-migrator (externos). Ver contrato anterior.

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

El Deployment lee `DATABASE_URL` del Secret `cotizacion-db`. Para el destino nuevo,
preparar un usuario de aplicacion aislado, no copiar las credenciales master
generadas por RDS. Las imagenes usan psycopg3: DSN `postgresql+psycopg://`,
con TLS/CA y caracteres de password codificados para URL.
Un operador autorizado entrega el DSN por un archivo privado nuevo, fuera de Git:

```bash
set -euo pipefail
: "${CONTEXT:?}" "${DATABASE_URL_FILE:?}"
kubectl --context "$CONTEXT" -n cotizacion create secret generic cotizacion-db \
  --from-file=DATABASE_URL="$DATABASE_URL_FILE" --dry-run=client -o yaml |
  kubectl --context "$CONTEXT" -n cotizacion apply -f -
```

No imprimir el archivo/YAML ni usar `set -x`. Si rota la contrasena hay que
sincronizar el Secret. External Secrets/CSI no estan instalados por este cambio;
su instalacion no es un prerrequisito implicitamente autorizado.

## Notas de operacion

- Las imagenes se publican con tag inmutable (la version, ej. `0.1.0`); no existe `latest`.
- La imagen es `linux/amd64` (nodos x86): en un Mac con Apple Silicon construir con
  `docker buildx build --platform linux/amd64`.
- `GET /ping` a traves de API Gateway lo responde el propio gateway (`Healthy Connection`) y no llega a
  los pods; probar con un endpoint real (`POST /v1/cotizaciones`). El health check del ALB si usa `/ping`
  del pod.
- Rotar coordinadamente los DSN de aplicacion/migracion y sus Secrets Kubernetes.
  La rotacion del master RDS no actualiza esos usuarios ni sus Secrets.
  IRSA por si solo no entrega variables de entorno ni instala External Secrets.

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
