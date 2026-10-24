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

## Pausa temporal de CodePipeline

`cicd_automatic_triggers_enabled = false` (valor predeterminado en este ambiente)
desactiva los disparadores por tags de los cinco pipelines: cotizacion, bff-web,
bff-movil, identidad y polizas. `DetectChanges = "false"` se conserva para que
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

## Tamaños y costo mensual aproximado

| Recurso | Tamaño | USD/mes |
|---|---|---|
| Control plane de EKS | 1 cluster | ~73 |
| Nodos | 2 x t3.small | ~30 |
| RDS PostgreSQL | db.t4g.micro, 20 GB, una zona | 0 en capa gratuita, ~15 después |
| NAT Gateway | 1 (no es HA entre zonas) | ~36 + tráfico |
| ALB interno | 1 | ~17 |
| KMS, Secrets Manager, ECR, S3, SNS/SQS | uso mínimo | ~2 |

Total orientativo: **~160 USD/mes** (~5 USD/día). El control plane de EKS, la NAT y el ALB **no**
están cubiertos por la capa gratuita. `t3.micro` no sirve como nodo: admite 4 pods y los pods de
sistema ya ocupan ese cupo.

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
`--bypass-governance-retention`. La base se destruye sin snapshot final.
