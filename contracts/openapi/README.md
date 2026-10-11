# Contratos OpenAPI

Esta carpeta contiene los contratos HTTP públicos y versionados de Solventa.

- `bff-web-v1.yaml`: contrato consumido por la aplicación web.

Los microservicios internos conservan sus propios modelos y se integran a través del BFF. Un
cambio incompatible requiere una nueva versión del contrato.

Para validar los contratos:

```bash
python scripts/validate_openapi.py
```
