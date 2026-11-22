# Contratos OpenAPI

Esta carpeta contiene los contratos HTTP públicos y versionados de Solventa.

- `bff-web-v1.yaml`: contrato consumido por la aplicación web.
- `cumplimiento-academico-v1.yaml`: API backend de reportes inmutables y reaseguros
  simulados, integrada al BFF Web; sin ACORD ni validez ante autoridades.
- `documento-poliza-web-v1.yaml`: documento HTML privado y aceptacion academica simulada,
  referenciado desde `bff-web-v1.yaml`. El hash identifica los bytes UTF-8 descargables.
- `bff-movil-v1.yaml`: contrato consumido por la aplicación móvil.
- `identidad-simulada-v1.yaml`: demostracion academica post-login de documento/prueba de vida,
  sin KYC real, imagenes ni PII; compartido por Identidad y BFF movil.
- `modificacion-poliza-movil-v1.yaml`: modificación versionada de pólizas para la app móvil,
  referenciada desde `bff-movil-v1.yaml`.
- `identidad-v1.yaml`: registro de usuarios y emisión de tokens JWT (OAuth 2.0, RS256). Lo
  consumen los BFF y el API Gateway, que valida los tokens con el JWKS publicado.
- `reportes-operativos-internal-v1.yaml`: fuentes internas sin datos personales (Polizas,
  Cotizacion y Siniestros) que el BFF Web consolida al mismo corte para los reportes
  operativos (SOL-322).

Los microservicios internos conservan sus propios modelos y se integran a través del BFF. Un
cambio incompatible requiere una nueva versión del contrato.

Para validar los contratos:

```bash
python scripts/validate_openapi.py
```
