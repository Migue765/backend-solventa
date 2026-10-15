# Manifiestos Kubernetes - bff-movil

Backend for Frontend de la app movil nativa. Mismo esquema que `deploy/k8s/bff-web`: delega la autenticacion en
`identidad` (`http://identidad.identidad.svc.cluster.local:8080`) y no tiene base de datos, Redis, cookies, CORS ni rol
IRSA. **`identidad` debe estar desplegado antes que `bff-movil`.**

- **Sin Job de migraciones.** El pipeline de `bff-movil` corre con `MIGRATE=false`.
- Sin CloudFront: la app llama directo al API Gateway (`terraform output -raw movil_api_url`), rutas `/movil/v1/...`,
  con `Authorization: Bearer <access token>` (60 minutos) y renovacion con refresh token (30 dias).

| Variable | Origen |
|---|---|
| `TARGET_GROUP_ARN` | `terraform output -raw bff_movil_target_group_arn` |
| `IMAGE` | `$(terraform output -raw bff_movil_ecr_repository_url):<tag>` |
| `JWT_ISSUER` | `terraform output -raw jwt_issuer` |
| `JWT_AUDIENCE` | `terraform output -raw jwt_audience` |
| `AWS_REGION` | `us-east-2` |

## Orden de aplicacion

Prerrequisitos: `terraform apply` de `terraform/environments/prod/primary`, `kubectl` apuntando al cluster e
`identidad` desplegado.

```bash
cd terraform/environments/prod/primary
export TARGET_GROUP_ARN=$(terraform output -raw bff_movil_target_group_arn)
export IMAGE="$(terraform output -raw bff_movil_ecr_repository_url):<tag>"
export JWT_ISSUER=$(terraform output -raw jwt_issuer)
export JWT_AUDIENCE=$(terraform output -raw jwt_audience)
export AWS_REGION=us-east-2
cd ../../../../deploy/k8s/bff-movil

# 1. Namespace y ServiceAccount (sin anotacion IRSA)
kubectl apply -f namespace.yaml -f serviceaccount.yaml

# 2. Servicio y deployment
kubectl apply -f service.yaml
envsubst < deployment.yaml | kubectl apply -f -

# 3. Autoscaling y disponibilidad
kubectl apply -f hpa.yaml -f pdb.yaml

# 4. Enlace al ALB interno (requiere el controller y el Service existente)
envsubst < targetgroupbinding.yaml | kubectl apply -f -
```

Verificacion: `kubectl -n bff-movil get pods,hpa,targetgroupbinding` y
`curl -i -X POST "$(terraform output -raw movil_api_url)/movil/v1/sesiones" -H 'Content-Type: application/json' -d '{}'`
(debe responder 400 del BFF; una ruta protegida sin token responde 401 del gateway).

## Notas de operacion

- Release: tag `bff-movil-v*` (disjunto de `v*` de cotizacion, `bff-web-v*` e `identidad-v*`). Imagen con tag inmutable.
- Imagen `linux/amd64`: en Apple Silicon usar `docker buildx build --platform linux/amd64`.
- Rutas publicas: `POST /movil/v1/sesiones`, `POST /movil/v1/sesiones/renovar` y `POST /movil/v1/usuarios`, con
  throttling de 5 req/s, burst 10 (limite global por ruta, no por cliente). El resto de `/movil/*` exige JWT.
