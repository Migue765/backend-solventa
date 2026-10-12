# bootstrap

Crea el bucket S3 de estado remoto (versionado, cifrado, sin acceso publico). Usa estado **local**.

```
terraform init && terraform apply
```

Los ambientes usan `backend "s3"` con `use_lockfile = true` (sin DynamoDB; requiere Terraform >= 1.10).
El bucket tiene `prevent_destroy`; el archivo de estado local de este modulo no debe perderse (esta excluido del control de versiones).
