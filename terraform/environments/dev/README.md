# Ambiente dev (Solventa - servicio Cotizacion)

Version minima y economica de la arquitectura objetivo. Cuenta `976083331509`, region `us-east-2`, perfil CLI `tesis`.

```
Cliente -> API Gateway (HTTP API) -> VPC Link V2 -> ALB interno -> TargetGroupBinding -> pods (EKS, 2 AZ)
                                                                          |-> Aurora PostgreSQL Serverless v2 (db cotizacion)
                                                                          |-> S3 Object Lock (evidencias WORM)
                                                                          |-> SNS -> SQS (+DLQ)
```

Todo cifrado con una CMK (rotacion anual). Sin WAF, Redis ni observabilidad (diferidos).

## Uso

```bash
# 1) Una sola vez: bucket de estado remoto
cd terraform/bootstrap && terraform init && terraform apply

# 2) Ambiente
cd ../environments/dev
cp terraform.tfvars.example terraform.tfvars   # editar public_access_cidrs (tu IP /32)
terraform init
terraform plan
terraform apply

# 3) Acceso al cluster y despliegue de la app
$(terraform output -raw kubeconfig_command)
# ver deploy/k8s/README.md
```

Requisitos: Terraform >= 1.11, AWS CLI v2 (el provider helm usa `aws eks get-token`), y que quien ejecute `apply`
sea una de las identidades de `eks_admin_principal_arns` (por defecto el usuario IAM `Migue`).
La primera vez, `helm_release` necesita alcanzar el endpoint publico de EKS desde tu IP (`public_access_cidrs`).

## Costo mensual aproximado (us-east-2, 24x7, aprox.)

| Recurso | USD/mes |
|---|---|
| EKS control plane | ~73 |
| 2 x t3.medium (nodos) | ~61 |
| NAT Gateway (1) + IP publica | ~36 + trafico |
| Aurora Serverless v2 (0.5 ACU minimo) + almacenamiento | ~45 |
| ALB interno | ~17 |
| KMS (1 CMK), Secrets Manager (1 secreto), SNS/SQS, S3, ECR, API GW | ~5 |
| **Total** | **~235-260 (~8 USD/dia)** |

Ahorro: destruir el ambiente cuando no se use (ver abajo); bajar `node_desired_size`/`node_min_size` a 1 si no se necesita HA.

## Destruir

```bash
kubectl delete -f ../../../deploy/k8s/ --ignore-not-found   # libera pods antes de destruir
cd terraform/environments/dev
terraform destroy
```

Notas al destruir:
- El bucket de evidencias tiene Object Lock: las versiones retenidas no se pueden borrar hasta que expire la retencion
  (1 dia por defecto). En modo GOVERNANCE se pueden borrar con `aws s3api delete-object --bypass-governance-retention`
  (requiere `s3:BypassGovernanceRetention`). En COMPLIANCE no hay forma de saltarse la retencion.
- Aurora se destruye sin snapshot final (`db_skip_final_snapshot = true`) y sin proteccion (solo dev).
- La CMK queda en "pending deletion" 7 dias (se factura hasta que se elimine).
- El bucket de estado (`bootstrap`) tiene `prevent_destroy` y se borra a mano.
