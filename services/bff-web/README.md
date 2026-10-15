# BFF Web

Backend for Frontend de la aplicación web de clientes de Solventa.

## Sesiones

El servicio expone:

| Método   | Ruta                  | Descripción              |
| -------- | --------------------- | ------------------------ |
| `POST`   | `/v1/sesiones`        | Inicia una sesión        |
| `GET`    | `/v1/sesiones/actual` | Consulta la sesión       |
| `DELETE` | `/v1/sesiones/actual` | Cierra la sesión vigente |
| `GET`    | `/ping`               | Comprueba disponibilidad |
| `GET`    | `/v1/productos-seguro` | Lista seguros disponibles |
| `POST`   | `/v1/solicitudes-cotizacion` | Registra una solicitud |
| `GET`    | `/v1/solicitudes-cotizacion/{id}` | Consulta una solicitud |

Las sesiones usan tokens opacos aleatorios. El cliente recibe el token únicamente mediante una
cookie `HttpOnly` y el repositorio almacena solo su hash. Los puertos de credenciales y sesiones
permiten sustituir los adaptadores locales por Cognito y Redis.

## Configuración local

`AUTH_DEVELOPMENT_USERS_JSON` recibe una lista JSON de usuarios. Las contraseñas se configuran
como hashes PBKDF2, nunca en texto plano.

```json
[
  {
    "id": "60fe1d7c-8c38-4e50-a3de-f532dd87b0ea",
    "nombre": "Cliente Solventa",
    "correo": "cliente@solventa.co",
    "rol": "cliente",
    "tipoDocumento": "cc",
    "identificacion": "1023456789",
    "telefono": "+573001234567",
    "passwordHash": "pbkdf2_sha256$600000$...$..."
  }
]
```

Variables disponibles:

| Variable                         | Default                 |
| -------------------------------- | ----------------------- |
| `AUTH_DEVELOPMENT_USERS_JSON`    | `[]`                    |
| `SESSION_DURATION_MINUTES`       | `30`                    |
| `SESSION_COOKIE_NAME`            | `solventa_session`      |
| `SESSION_COOKIE_SECURE`          | `true`                  |
| `SESSION_COOKIE_SAMESITE`        | `Lax`                   |
| `AUTH_MAX_ATTEMPTS`              | `5`                     |
| `AUTH_ATTEMPT_WINDOW_SECONDS`    | `300`                   |
| `CORS_ALLOWED_ORIGINS`           | `http://localhost:4200` |
| `QUOTE_SERVICE_URL`              | `http://localhost:5000` |

Para desarrollo HTTP local se debe establecer `SESSION_COOKIE_SECURE=false`.

## Ejecución

```bash
pip install -e ".[dev]"
flask --app bff_web.app run --port 5001
pytest
```
