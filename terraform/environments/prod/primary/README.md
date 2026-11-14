# Ambiente prod (Solventa - servicio Cotizacion)

Reconstruccion vacia en `396383602233/us-east-2`, con datos de prueba nuevos.
No copiar cuentas, claves, sesiones, consentimientos, polizas ni evidencias del origen
o de las fixtures locales. Conservar el origen sin borrados. El tamano configurado
no acredita elegibilidad FREE ni gratuidad; confirmar el plan de cuenta y el costo.

**No aplicar esta revision sobre el state existente del origen ni un primary anterior.**
La red y los 13 SG declarados ahora pertenecen a `../network`, con state nuevo y
separado; primary requiere sus IDs reales y no los recrea. No hay migracion entre
roots. Los dos releases Helm
ahora pertenecen al root `../addons`, con state nuevo y separado: hacerlo sobre el
state viejo planearia retirarlos. Los nodos nuevos usan launch template y disco cifrado;
ese cambio exige reemplazar un node group previo que no usara plantilla propia.
No usar `init -migrate-state`, imports ni `-target`
para reconstruir. Crear un state vacio; esta documentacion no autoriza su creacion.

```
Cliente --> API Gateway (HTTP API) --VPC Link--> ALB interno --> EKS (pods cotizacion)
                                                                   |-> RDS PostgreSQL (db.t4g.micro)
                                                                   |-> S3 evidencias (Object Lock)
                                                                   |-> SNS -> SQS (+ DLQ)
```

## Preparacion y primer despliegue

El [runbook de arranque vacio](../../../../deploy/k8s/ARRANQUE-VACIO.md)
detalla Secrets/DSN, orden de las nueve APIs, readiness, cutover y rollback.

### Gate IAM: boundary externa pendiente de diseno y aprobacion

`permissions_boundary_arns` es un objeto obligatorio con tres managed policy ARN
distintos de `account_id`, sin comodines: `network` para cluster/nodo/ALB (3),
`ci` para los 27 roles CI y `application` para Cotizacion/Identidad/Siniestros (3).
`iam-irsa` recibe dos parametros distintos para ALB y Cotizacion; no se reutiliza
una boundary comun. Los modulos reutilizables conservan `null` como default para no
adjuntar boundaries a otros entornos sin autorizacion. Este root no crea ni modifica
la policy externa. Su ARN no acredita el contenido ni su eficacia.

El administrador externo debe revisar la policy y su version/hash, impedir que el
rol de provisioning/operacion/runtime pueda editarla, borrarla, cambiar su version
activa o quitar/sustituir la boundary de los roles; debe exigirla al crear roles.
Esas restricciones pertenecen a la identidad administrativa externa, no las
implementa esta variable ni puede garantizar su cumplimiento Terraform.
No usar una policy permisiva para superar el gate.

**Provisioning bloqueado:** falta resolver las escrituras de red CI/CNI/controlador
con compatibilidad por accion/recurso. `restrict_service_access` ya acota ENI en
Perfilamiento pero tambien cambia trust, conexion y egress a80/443; no se activa
en los otros ocho pipelines por adjuntar esta boundary. EKS conserva la policy
AWS CNI y el controlador conserva su policy actual, incluido el Allow amplio SG.
Este cambio parcial no corrige esos permisos ni acredita aislamiento de Jenkins.

Propuesta para cerrar el diseno: revisar una boundary externa que limite
mutaciones a VPC/subnets/SG/ENI/TG propios y proteja explicitamente recursos Jenkins,
sin basarse solo en tags ni permitir retaggear recursos ajenos. Separar permisos
de creacion y operaciones sobre recursos existentes segun SAR por accion.
Para CNI comprobar autorizacion simultanea de instancia/ENI en Attach/Detach,
creacion/tagging y asignacion/liberacion IP. No aplicar ec2:Vpc indiscriminadamente
ni sustituir la policy AWS por un recorte no probado. No agregar un rol CNI nuevo.

Se eligio preparar primero la red en un root independiente: ver
[contrato y orden](../network/README.md). La VPC y los 13 SG deben existir y sus
metadatos ser revisados antes de preparar los limites literales y primary.
No se implementan tags dinamicos de rol. El SG automatico de EKS queda fuera de
network; CNI requiere ademas condiciones por instancia/ENI compatibles y controles
de perfil/antirretag que el administrador externo debe verificar.
Los SLR de cuenta/servicio quedan fuera de estos 33 roles y sus boundaries.
No prometer que una denylist historica de dos instancias cubra ENI/rutas/roles.
Tras resolverlo Despliegue necesita plan protegido NUEVO y baseline Autopilot del
plan JSON real; no reutilizar el inventario de 300 altas ni deducir permisos de .tf.
Con runtime Autopilot aprobado, el comando a preparar sobre el nuevo JSON protegido es
`uvx iam-policy-autopilot@latest generate-policies RUTA_ABSOLUTA_PLAN_JSON --region us-east-2 --account 396383602233 --pretty`.
No ejecutarlo hoy ni imprimir el plan, descargar binarios o subir policies.

Especialista-Despliegue ejecuta cada fase solo con el plan, revision y autorizacion
correspondientes. Antes de empezar faltan el perfil real de rol asumido, sus roles
administradores EKS, CIDRs aprobados y confirmacion del plan de cuenta/servicios.
Los ejemplos dejan variables obligatorias vacias y un marcador no operativo en el
perfil del backend. No usar root, el perfil historico `tesis` ni el state del origen.

1. Verificar STS con perfil explicito: cuenta `396383602233`, ARN
   `arn:aws:sts::396383602233:assumed-role/...`, region `us-east-2`.
   Los cuatro roots rechazan root/usuarios IAM y usan `allowed_account_ids`.
   Copiar los ejemplos a archivos locales ignorados y completarlos por canal seguro;
   los campos de backend y provider son independientes y deben coincidir.
   El backend S3 se inicializa antes de evaluar las guardas del provider: comprobar
   tambien su identidad STS antes de `init`, sin heredar credenciales root.
   Repetir STS antes de aplicar un plan guardado: la postcondicion valida el plan,
   no evita que alguien cambie credenciales entre `plan` y `apply`.
2. Revisar baseline Jenkins antes y despues: stack CloudFormation
   `solventa-jenkins-pilot`, VPC default `vpc-0c57079b75d10cccf`, controlador
   `i-08baf9711e9190923` running y agente `i-004b8940113a648c1` stopped.
   No compartir roles, SG, rutas, volumenes ni state. El backend usa VPC propia,
   recursos etiquetados `Component=backend` y su bucket de estado nuevo.
3. Bootstrap: completar `terraform/bootstrap/terraform.tfvars` desde su ejemplo.
   Tras autorizacion, revisar/aplicar el plan de ese root, que guarda state local.
   Proteger ese archivo: no subirlo a Git ni mezclarlo con el state de Jenkins.
4. Network antes de primary: seguir `../network/README.md`, con key nueva
   `environments/prod/network/terraform.tfstate`. Tras su aplicacion autorizada,
   exportar solo `network_context` a `network.auto.tfvars.json` bajo esa misma
   clave. No usar remote_state ni copiar state. Los 13 SG nacen sin reglas;
   primary conserva las reglas existentes y los enlaces al SG automatico EKS.
5. Primary: completar `terraform.tfvars` y `backend.hcl` desde los ejemplos.
   El ejemplo backend incluye cuenta permitida `396383602233`, Ohio y key propia;
   confirmar que el bucket sea el output real del bootstrap, no asumir que ya existe.
   Tras autorizacion, inicializar con `-backend-config=backend.hcl`, revisar el plan
   guardado y aplicar solo ese plan. Primary crea infraestructura AWS sin requerir
   conexion Helm a un cluster todavia inexistente. Exige `network_context` tipado
   y lecturas EC2 que comprueban VPC/cuenta, nombres SG, Tier y dos AZ antes de
   aceptar los IDs; no recrea la red ni sus SG. Un plan anterior al split no sirve.
6. Addons: despues del primary aprobado y terminado, leer **solo**
   `terraform output -json cluster_addons` dentro de primary. Guardar ese objeto
   bajo la clave `cluster_addons` de `../addons/terraform.tfvars.json`.
   No usar `terraform output -json` sin nombre ni copiar el state que contiene claves.
   Completar `../addons/backend.hcl`: su key es distinta de primary.
   Con autorizacion separada, inicializar/revisar/aplicar addons. Lee el cluster
   existente por nombre, comprueba su VPC e instala los mismos dos charts y versiones.
7. Aprovisionar bases/usuarios nuevos por dominio y Secrets externos mediante el
   procedimiento aprobado; ninguna base de aplicacion debe usar el usuario master
   como solucion rapida. Los BFF no necesitan DB. Usar migraciones de esquema
   existentes, sin restaurar dumps/snapshots/filas historicos.
8. Inicializar namespaces, ServiceAccounts/IRSA y Secrets antes de los Jobs. Para
   Cotizacion, Identidad, Perfilamiento y BFF, aplicar Service y Deployment semilla
   renderizados con imagen aprobada y **replicas cero**, sin HPA todavia: no arrancar
   pods contra un esquema ausente. `deploy.sh` migra primero y luego escala/actualiza
   ese Deployment; no lo crea. Completar PDB; diferir los TargetGroupBindings de
   ambos BFF hasta activar/verificar JWE en privado, segun la compuerta del runbook.
   Los bindings de Cotizacion/Identidad no abren login por si solos. El
   pipeline aplica HPA despues de migrar. Para los BFF conservar `MIGRATE=false`.
   Polizas/Siniestros/Notificaciones/Asistencias admiten su primer Deployment
   mediante el pipeline, siempre despues de preparar sus dependencias y migraciones.
9. Revisar las variables de renderizado, sin imprimir sus valores secretos:
   `IMAGE`, `AWS_REGION`, `IRSA_ROLE_ARN`, `TARGET_GROUP_ARN`, `JWT_ISSUER`,
   `JWT_AUDIENCE`, `LOGIN_ENCRYPTION_SECRET_NAME` y, para Siniestros,
   `SINIESTROS_EVIDENCIAS_BUCKET` desde el output del mismo nombre funcional
   `siniestros_evidence_bucket`. CodeBuild ya recibe ese bucket desde Terraform.
   No ejecutar `envsubst` con campos requeridos vacios. Para secretos nuevos usar
   `--from-file`, no las recetas historicas de copia del master. Consultar migraciones y
   Secrets en `deploy/k8s/<servicio>/README.md` (Cotizacion: `deploy/k8s/README.md`).
10. Conservar el alta inicial de flags apagados hasta preparar criptografia/Redis.
   Habilitar JWE en Identidad y despues BFF Web mediante
   `scripts/configure_encrypted_login.py` y `docs/operacion-login-cifrado.md`.
   Usar el helper existente, contexto e imagen
   explicitos y clave JWE nueva, distinta de JWT. No publicar el nuevo frontend
   hasta comprobar login cifrado, errores y roles. Biometria requiere decision
   de activacion coordinada con Android; no inferirla del cambio de cuenta.
11. Configurar las URL internas de Polizas/Notificaciones en Cotizacion segun sus
    runbooks, y activar consumidores solo con dependencias listas. Crear fixtures
    sinteticas **nuevas** por los mecanismos existentes tras aprobar las pruebas;
    no subir la fixture SQLite local ni copiar sus passwords. Conservar rotulos
    de simulacion de pagos/fuentes/prestadores. Verificar persistencia y propiedad
    entre clientes, no solo `/ping`.

El API nuevo obtiene su URL/issuer de outputs: actualizar consumidores, CORS, TLS
y DNS solo tras autorizar el cutover. No versionar URLs calculadas de otro entorno.
Las claves JWT/JWE nuevas implican login nuevo; esta reconstruccion no recupera
cuentas ni siniestros anteriores. Conservar el origen para una decision posterior.

Los pipelines mantienen triggers automaticos apagados y aprobacion manual antes de
Deploy. Su creacion/autorizacion de conexion puede iniciar Source/Build; revisar esa
ejecucion y no aprobar Deploy antes de preparar recursos/configuracion/migraciones.
La pausa de triggers no equivale a impedir la ejecucion inicial de un pipeline.

### Comprobaciones offline

`python -m pytest tests/test_academic_infrastructure.py tests/test_biometric_deployment.py
tests/test_terraform_perfilamiento_pipeline.py` ejecuta dobles y renderizado local,
sin despliegues. En un host con Terraform y providers ya disponibles, ejecutar
`terraform fmt -check -recursive terraform` y validacion local con backend deshabilitado
en cada root; no inicializar el backend remoto para estas comprobaciones.
`terraform -chdir=terraform/bootstrap test` usa `mock_provider` para rechazar root,
usuarios y roles de otra cuenta. No sustituye una revision del plan ejecutable.

## Pausa temporal de CodePipeline

`cicd_automatic_triggers_enabled = false` (valor predeterminado en este ambiente)
desactiva los disparadores por tags de todos los pipelines: cotizacion, bff-web,
bff-movil, identidad, polizas, perfilamiento, siniestros, notificaciones y
asistencias. `DetectChanges = "false"` se conserva para que
los cambios de rama tampoco inicien ejecuciones. No se eliminan pipelines,
proyectos de CodeBuild ni recursos de la aplicacion desplegada.

Esta pausa solo evita nuevas ejecuciones automaticas: no detiene ejecuciones
en curso y permite iniciar CodePipeline o CodeBuild manualmente, lo cual sigue
consumiendo minutos. Antes de aplicar, revisar y detener ejecuciones pendientes
si las hay, cuidando de no interrumpir migraciones o despliegues en curso.
No revierte consumo ni cargos ya generados, ni elimina los costos de otros
servicios de AWS.

Para reactivar, establecer `cicd_automatic_triggers_enabled = true` en
`terraform.tfvars`, revisar `terraform plan` y aplicar el plan aprobado.
Los patrones de tags originales se conservan.

## Rutas del BFF web

El ALB envia las rutas de usuarios, sesiones, productos, solicitudes y clientes al BFF web.
`/v1/destinos-viaje` usa una regla exacta independiente (prioridad 11): la regla web de
prioridad 10 ya contiene el maximo de cinco patrones. No se debe ampliar a `/v1/*`, pues
eso desviaria tambien las rutas internas de cotizacion.

El API nuevo incluye sus rutas OPTIONS. Si aparece un conflicto con recursos
preexistentes, detener el plan y revisar cuenta/state en lugar de importarlos.
Esta reconstruccion no adopta recursos del origen ni de Jenkins.

Una integracion de PR no actualiza los contenedores de AWS. Para una validacion autorizada
con una revision integrada de `develop`, se puede fijar el commit sin cambiar la rama
`main` configurada ni los disparadores de los pipelines:

```bash
aws codepipeline start-pipeline-execution \
  --name solventa-prod-cotizacion --profile "$AWS_PROFILE" --region us-east-2 \
  --source-revisions actionName=Source,revisionType=COMMIT_ID,revisionValue=<sha-completo>
aws codepipeline start-pipeline-execution \
  --name solventa-prod-bff-web --profile "$AWS_PROFILE" --region us-east-2 \
  --source-revisions actionName=Source,revisionType=COMMIT_ID,revisionValue=<sha-completo>
```

Verificar ambos resultados de despliegue, las imagenes de los pods y los endpoints publicos;
un build exitoso no demuestra un rollout exitoso. Los tags ECR son inmutables: si esa
imagen ya existe, no repetir su publicacion; usar la ejecucion o imagen ya construida.
El clúster necesita espacio para el Job de migracion y el pod adicional de cada rollout.
Si alcanza el limite de pods, no borrar previews ni servicios ajenos para liberarlo.
Cualquier reduccion temporal autorizada de replicas requiere ajustar tambien su HPA y
restaurar ambos valores al terminar; de lo contrario, el HPA revierte la reduccion.

## Tamaños y costo mensual aproximado

| Recurso | Tamaño | USD/mes |
|---|---|---|
| Control plane de EKS | 1 cluster | ~73 |
| Nodos | 3 x t3.small | ~45 |
| RDS PostgreSQL | db.t4g.micro, 20 GB, una zona | ~14 antes de franquicias/creditos |
| NAT Gateway | 1 (no es HA entre zonas) | ~36 + tráfico |
| ALB interno | 1 | ~17 |
| Redis | cache.t4g.micro | ~12 |
| EBS nodos | 90 GiB gp3 | ~7 |
| KMS, Secrets Manager, ECR, S3, SNS/SQS | depende del uso y secretos externos | aparte |

El inventario del 13/11/2026 estima **206.537 USD/mes fijos**, con 730 horas,
antes de trafico, LCU, builds, volumen real almacenado, impuestos y Jenkins.
No asumir que un plan FREE o su saldo financia un mes de operacion. `t3.micro`
no sirve como nodo: admite 4 pods y los pods de
sistema ya ocupan ese cupo. Cada `t3.small` admite 11 pods: con nueve APIs, los pods de sistema,
los CronJobs y el pod extra de cada rollout, dos nodos no dejan margen.

El node group ignora cambios de `desired_size`. Para pasar de 2 a 3 nodos, primero subir el
tamaño con la CLI (mismo perfil y región que `kubeconfig_command`) y después aplicar el mismo
valor en `terraform.tfvars`:

```bash
cluster="$(terraform output -raw cluster_name)"
aws eks update-nodegroup-config --cluster-name "$cluster" \
  --nodegroup-name "${cluster}-default" \
  --scaling-config minSize=3,maxSize=3,desiredSize=3
```

## Evidencias de siniestros

`siniestros.tf` crea el bucket `solventa-prod-siniestros-evidencias-<cuenta>` (SSE-KMS con la
CMK, solo TLS, sin acceso público, versionado, sin Object Lock) y el rol IRSA
`solventa-prod-siniestros`. El rol solo puede leer, escribir y borrar objetos bajo
`evidencias/` de un bucket de esta cuenta; no puede borrar versiones. Un borrado deja un
marcador y la versión previa se elimina definitivamente a los 7 días
(`noncurrent_version_retention_days`). El output `siniestros_role_arn` es la anotación del
ServiceAccount `siniestros/siniestros`.

## Antes del lanzamiento

- `db_deletion_protection = true`, `db_skip_final_snapshot = false`, `ecr_force_delete = false`.
- `multi_az` en la base y una NAT por zona; Object Lock en modo COMPLIANCE con la retención real.
- HTTPS en el listener del ALB (`api_listener_protocol`, `api_certificate_arn`).

## Destruir

```bash
terraform destroy
```

El bucket de evidencias tiene Object Lock en GOVERNANCE con 1 día de retención: el `destroy` falla
mientras existan versiones retenidas; espera a que expire o borra con
`--bypass-governance-retention`. El bucket de evidencias de siniestros no se vacía solo: el
`destroy` falla mientras tenga objetos o versiones. La base nueva tiene proteccion contra borrado y exige snapshot final; cualquier retirada
requiere autorizacion separada, revisar las retenciones y preservar los datos.
