# Manifiestos Kubernetes - bff-web

Backend for Frontend de la web de clientes. Compone cotizaciones y delega la autenticacion en `identidad`
(`http://identidad.identidad.svc.cluster.local:8080`). Mismo esquema que `deploy/k8s` (cotizacion): los manifiestos con
variables `${...}` se sustituyen con `envsubst` a partir de los outputs de Terraform.

No tiene base de datos, ni Redis, ni cookies, ni CORS (el API Gateway valida el JWT y responde los preflight), ni
rol IRSA: los usuarios, las contrasenas y las sesiones son de `identidad`. **`identidad` y `cotizacion` deben estar
desplegados antes que `bff-web`.** El pipeline de `bff-web` corre con `MIGRATE=false` y no tiene Job de migracion.

| Variable | Origen |
|---|---|
| `TARGET_GROUP_ARN` | `terraform output -raw bff_web_target_group_arn` |
| `IMAGE` | `$(terraform output -raw bff_web_ecr_repository_url):<tag>` |
| `JWT_ISSUER` | `terraform output -raw jwt_issuer` |
| `JWT_AUDIENCE` | `terraform output -raw jwt_audience` |
| `AWS_REGION` | `us-east-2` |

## Orden de aplicacion

Prerrequisito: `terraform apply` de `terraform/environments/prod/primary` y `kubectl` apuntando al cluster
(`terraform output kubeconfig_command`). `cotizacion` se consume en `http://cotizacion.cotizacion.svc.cluster.local:8080`.

```bash
cd terraform/environments/prod/primary
export TARGET_GROUP_ARN=$(terraform output -raw bff_web_target_group_arn)
export IMAGE="$(terraform output -raw bff_web_ecr_repository_url):<tag>"
export JWT_ISSUER=$(terraform output -raw jwt_issuer)
export JWT_AUDIENCE=$(terraform output -raw jwt_audience)
export AWS_REGION=us-east-2
cd ../../../../deploy/k8s/bff-web

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

Verificacion: `kubectl -n bff-web get pods,hpa,targetgroupbinding` y, con el API Gateway:

```bash
API=$(cd ../../../terraform/environments/prod/primary && terraform output -raw jwt_issuer)
curl -i "$API/v1/productos-seguro"                                   # sin token: 401 del gateway
curl -i -X POST "$API/v1/sesiones" -H 'Content-Type: application/json' -d '{}'   # 400 (ruta publica, llega al BFF)
```

## Notas de operacion

- Las imagenes llevan tag inmutable (`<version>-<commit7>`); no existe `latest`.
- Imagen `linux/amd64`: en Apple Silicon usar `docker buildx build --platform linux/amd64`.
- Rutas publicas (sin token): `POST /v1/sesiones`, `POST /v1/sesiones/renovar` y `POST /v1/usuarios`, con throttling de
  5 req/s, burst 10 (limite global por ruta, no por cliente). Todo lo demas de `/v1/*` exige un access token valido.
- El frontend web llama directo al API Gateway con `Authorization: Bearer <access token>`; el origen del frontend debe
  estar en `cors_allowed_origins` de Terraform.
