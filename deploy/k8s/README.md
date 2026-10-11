# Manifiestos Kubernetes - cotizacion

Los manifiestos `serviceaccount`, `deployment` y `targetgroupbinding` usan variables `${...}` que se sustituyen con
`envsubst` a partir de los outputs de Terraform.

| Variable | Origen |
|---|---|
| `IRSA_ROLE_ARN` | `terraform output -raw service_role_arn` |
| `TARGET_GROUP_ARN` | `terraform output -raw target_group_arn` |
| `IMAGE` | `$(terraform output -raw ecr_repository_url):<tag>` |
| `AWS_REGION` | `us-east-2` |

## Orden de aplicacion

Prerrequisito: `terraform apply` de `terraform/environments/dev` (instala AWS Load Balancer Controller y metrics-server)
y `kubectl` apuntando al cluster (`terraform output kubeconfig_command`).

```bash
cd terraform/environments/dev
export IRSA_ROLE_ARN=$(terraform output -raw service_role_arn)
export TARGET_GROUP_ARN=$(terraform output -raw target_group_arn)
export IMAGE="$(terraform output -raw ecr_repository_url):<tag>"
export AWS_REGION=us-east-2
cd ../../../deploy/k8s

# 1. Namespace
kubectl apply -f namespace.yaml

# 2. Secreto DATABASE_URL (externo: no se versiona). Ver nota abajo.

# 3. ServiceAccount (anotacion IRSA)
envsubst < serviceaccount.yaml | kubectl apply -f -

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

El deployment lee `DATABASE_URL` del Secret `cotizacion-db`. Las credenciales viven en Secrets Manager
(`terraform output db_secret_arn`, generado por RDS). Opciones:

- Recomendada: External Secrets Operator o Secrets Store CSI Driver sincronizando el secreto de Secrets Manager
  (el rol IRSA del servicio ya puede leerlo). No se instala por Terraform en dev.
- Manual (dev), con una identidad que tenga acceso al secreto:

```bash
SECRET=$(aws secretsmanager get-secret-value --secret-id "$(terraform output -raw db_secret_arn)" \
  --query SecretString --output text --profile tesis --region us-east-2)
DB_USER=$(echo "$SECRET" | jq -r .username)
DB_PASS=$(echo "$SECRET" | jq -r .password | jq -sRr @uri)
kubectl -n cotizacion create secret generic cotizacion-db \
  --from-literal=DATABASE_URL="postgresql://${DB_USER}:${DB_PASS}@$(terraform output -raw db_endpoint):5432/cotizacion"
```

Si la contrasena cambia (rotacion), hay que volver a sincronizar el Secret.
