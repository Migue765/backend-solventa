# BFF Móvil

Backend for Frontend de la aplicación móvil de clientes de Solventa. Contrato:
`contracts/openapi/bff-movil-v1.yaml`.

## Rol en la arquitectura

Igual que `bff-web`, es una **fachada sin estado**: no tiene base de datos, Redis, cookies ni
sesiones en el servidor, y no es dueño de usuarios ni de contraseñas (D-02 y D-05).
`services/identidad` es la dueña de los usuarios y emite los tokens; el BFF le delega el
registro, el login, la renovación y la revocación, y valida el JWT localmente (firma con el
JWKS de identidad, `iss`, `aud`, `exp` con 10 s de tolerancia y `client_id = movil`). Rechaza
`alg: none`, HS256 y cualquier algoritmo distinto de RS256. El API Gateway valida el mismo
token antes de llegar aquí.

```
App ──Bearer JWT──> API Gateway ──> bff-movil ──> identidad
```

## Endpoints

El prefijo `/movil` permite compartir el API Gateway con `bff-web`.

| Método   | Ruta                           | Auth   | Descripción                                        |
| -------- | ------------------------------ | ------ | -------------------------------------------------- |
| `POST`   | `/movil/v1/usuarios`           | No     | Registra un usuario cliente (proxy a identidad)    |
| `POST`   | `/movil/v1/sesiones`           | No     | Inicia sesión: access token + refresh token        |
| `POST`   | `/movil/v1/sesiones/renovar`   | No     | Rota el refresh token y emite nuevos tokens        |
| `GET`    | `/movil/v1/sesiones/actual`    | Bearer | Usuario y vencimiento tomados del JWT              |
| `DELETE` | `/movil/v1/sesiones/actual`    | Bearer | Cierra sesión; revoca el `refreshToken` si se envía |
| `GET`    | `/ping`                        | No     | Comprueba disponibilidad                           |

El access token dura 1 hora y el refresh token es opaco, de un solo uso y rota en cada
renovación. La app guarda ambos en el almacén seguro del dispositivo (Keychain/Keystore) y no
hay CORS. Los errores usan `application/problem+json`; si identidad o su JWKS no responden, la
respuesta es `503 servicio_no_disponible` (nunca `401`) para no cerrar una sesión válida por una
caída. Un `429` conserva `Retry-After`.

## Acceso con clave protegida por biometria

Seis rutas nuevas bajo `/movil/v1/biometria/...` y
`POST /movil/v1/sesiones/biometria` delegan a Identidad, sin estado en el BFF.
`BIOMETRIC_LOGIN_ENABLED` acepta `true`/`false`, por defecto `false`.
El alta exige bearer movil y contrasena actual; login verifica firma P-256,
no un booleano local. El formato de sesion, refresh y logout no cambia.
No se certifican hardware, identidad civil, selfie ni prueba de vida.
Ver [contrato y limites](../../docs/acceso-biometrico.md).

## Configuración

| Variable                    | Default                                    | Descripción                    |
| --------------------------- | ------------------------------------------ | ------------------------------ |
| `IDENTIDAD_URL`             | requerida                                  | URL interna de identidad       |
| `JWT_ISSUER`                | requerida                                  | `iss` esperado en los tokens   |
| `JWT_AUDIENCE`              | `solventa-api`                             | `aud` esperado                 |
| `JWKS_URL`                  | `IDENTIDAD_URL` + `/.well-known/jwks.json` | Origen de las claves públicas  |
| `IDENTIDAD_TIMEOUT_SECONDS` | `5`                                        | Timeout hacia identidad y JWKS |

## Ejecución

```bash
pip install -e ".[dev]"
pytest
IDENTIDAD_URL=http://localhost:5003 JWT_ISSUER=http://localhost:5003 \
  flask --app bff_movil.app run --port 5002
```
