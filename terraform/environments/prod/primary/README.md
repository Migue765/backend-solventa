# Ambiente prod (Solventa - servicio Cotizacion)

Único ambiente por ahora: se prueba todo aquí porque no habrá tráfico real hasta el lanzamiento.
Está dimensionado al mínimo viable para no salirse del plan gratuito de AWS; los tamaños son
variables y se amplían sin cambiar la arquitectura.

```
Cliente --> API Gateway (HTTP API) --VPC Link--> ALB interno --> EKS (pods cotizacion)
                                                                   |-> RDS PostgreSQL (db.t4g.micro)
                                                                   |-> S3 evidencias (Object Lock)
                                                                   |-> SNS -> SQS (+ DLQ)
```

## Orden de ejecución

```bash
# 1. Una sola vez: bucket de estado remoto
cd terraform/bootstrap && terraform init && terraform apply

# 2. Infraestructura (usa terraform.tfvars con tu IP /32; ver terraform.tfvars.example)
cd ../environments/prod/primary
terraform init
terraform plan -out=prod.tfplan
terraform apply prod.tfplan
```

Quien ejecuta debe ser el usuario IAM `Migue` (o estar en `eks_admin_principal_arns`): el provider de
Helm usa esa identidad para entrar al cluster.

El modulo EKS conserva los CIDR de `public_access_cidrs` y agrega `152.202.122.240/32`
en el codigo del ambiente, sin duplicados. Asi, un `terraform.tfvars` anterior no elimina
ese acceso. Ejecutar siempre desde una revision que incluya este ajuste; una revision
anterior todavia puede quitarlo. Si cambia la IP publica del equipo, actualizar este CIDR.

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

Una integracion de PR no actualiza los contenedores de AWS. Para una validacion autorizada
con una revision integrada de `develop`, se puede fijar el commit sin cambiar la rama
`main` configurada ni los disparadores de los pipelines:

```bash
aws codepipeline start-pipeline-execution \
  --name solventa-prod-cotizacion --region us-east-2 \
  --source-revisions actionName=Source,revisionType=COMMIT_ID,revisionValue=<sha-completo>
aws codepipeline start-pipeline-execution \
  --name solventa-prod-bff-web --region us-east-2 \
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
| RDS PostgreSQL | db.t4g.micro, 20 GB, una zona | 0 en capa gratuita, ~15 después |
| NAT Gateway | 1 (no es HA entre zonas) | ~36 + tráfico |
| ALB interno | 1 | ~17 |
| KMS, Secrets Manager, ECR, S3, SNS/SQS | uso mínimo | ~2 |

Total orientativo: **~175 USD/mes** (~6 USD/día). El control plane de EKS, la NAT y el ALB **no**
están cubiertos por la capa gratuita. `t3.micro` no sirve como nodo: admite 4 pods y los pods de
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
`destroy` falla mientras tenga objetos o versiones. La base se destruye sin snapshot final.
