# BFF Web

Backend for Frontend de la aplicación web de clientes de Solventa. Contrato:
`contracts/openapi/bff-web-v1.yaml`.

## Rol en la arquitectura

El BFF es una **fachada sin estado** para el canal web: compone y adapta contratos, pero no es
dueño de datos ni de reglas de negocio (D-02 y D-05 del documento de arquitectura).

- **Identidad** (`services/identidad`) es la dueña de los usuarios, de las contraseñas y de la
  emisión de tokens. El BFF le delega el registro, el login, la renovación y la revocación.
- **No hay base de datos, Redis, cookies ni sesiones en el servidor.** La sesión es un JWT de
  acceso (RS256, 1 hora) más un refresh token opaco de un solo uso que rota en cada renovación.
- **Validación en dos capas.** El API Gateway valida el JWT con su authorizer y el BFF lo vuelve
  a validar (firma con el JWKS de identidad, `iss`, `aud`, `exp` con 10 s de tolerancia y
  `client_id = web`). Se rechazan `alg: none`, HS256 y cualquier algoritmo distinto de RS256.
- **Cotizaciones** sigue siendo del servicio `cotizacion`; el BFF solo le pasa el `sub` del token
  como identificador del cliente.

```
Web ──Bearer JWT──> API Gateway ──> bff-web ──> identidad   (usuarios, tokens, JWKS)
                                         └────> cotizacion  (solicitudes)
```

## Endpoints

| Método   | Ruta                              | Auth   | Descripción                                   |
| -------- | --------------------------------- | ------ | --------------------------------------------- |
| `POST`   | `/v1/usuarios`                    | No     | Registra un usuario cliente (proxy a identidad) |
| `POST`   | `/v1/sesiones`                    | No     | Inicia sesión: access token + refresh token   |
| `POST`   | `/v1/sesiones/renovar`            | No     | Rota el refresh token y emite nuevos tokens   |
| `GET`    | `/v1/sesiones/actual`             | Bearer | Usuario y vencimiento tomados del JWT         |
| `DELETE` | `/v1/sesiones/actual`             | Bearer | Cierra sesión; revoca el `refreshToken` si se envía |
| `GET`    | `/v1/productos-seguro`            | Bearer | Lista los seguros disponibles                 |
| `POST`   | `/v1/solicitudes-cotizacion`      | Bearer | Registra una solicitud (`Idempotency-Key`)    |
| `GET`    | `/v1/solicitudes-cotizacion/{id}` | Bearer | Consulta una solicitud                        |
| `GET`    | `/ping`                           | No     | Comprueba disponibilidad                      |

Los errores usan `application/problem+json` con `code` y `correlationId`. Si identidad o su JWKS
no responden, el BFF contesta `503 servicio_no_disponible` (nunca `401`), para que el cliente no
cierre una sesión válida por una caída. Un `429 demasiados_intentos` conserva `Retry-After`.
`X-Correlation-ID` se propaga a identidad.

### Importes y periodicidad de ofertas

Las ofertas y GET/PUT de selección transmiten `prima: {monto, moneda}` y
`periodicidad: mensual | por_viaje | por_vuelo` calculadas y persistidas por cotización.
El BFF no calcula precios ni transforma documentos de identidad en riesgo.
`primaMensual` mantiene el importe solo para `mensual`; es `null` para viaje/vuelo.
Los consumidores deben leer `prima` y usar la periodicidad para la etiqueta del importe:
**el contrato no es retrocompatible con clientes que asumen `primaMensual` no nulo**.
El adaptador acepta respuestas antiguas como mensuales únicamente si faltan ambos campos nuevos.
La selección sigue recibiendo `{ofertaId, version}` con `Idempotency-Key`.

### Cómo usa los tokens el frontend

El access token vive solo en memoria; el refresh token, en `sessionStorage`. Cuando el access
token está por vencer (`expiraEn`) o responde 401, el cliente llama a `/v1/sesiones/renovar` y
reemplaza ambos tokens: el refresh anterior deja de servir.

## Configuración

| Variable                    | Default                 | Descripción                                          |
| --------------------------- | ----------------------- | ---------------------------------------------------- |
| `IDENTIDAD_URL`             | requerida               | URL interna de identidad                             |
| `JWT_ISSUER`                | requerida               | `iss` esperado en los tokens                         |
| `JWT_AUDIENCE`              | `solventa-api`          | `aud` esperado                                       |
| `JWKS_URL`                  | `IDENTIDAD_URL` + `/.well-known/jwks.json` | Origen de las claves públicas |
| `IDENTIDAD_TIMEOUT_SECONDS` | `5`                     | Timeout de las llamadas a identidad y al JWKS        |
| `QUOTE_SERVICE_URL`         | `http://localhost:5000` | Servicio de cotización                               |

Las claves públicas se cachean 600 s. Ante un `kid` desconocido se recarga el JWKS, como máximo
una vez cada 10 s, para tolerar rotaciones sin permitir que un `kid` falso sature a identidad.

## Ejecución

```bash
pip install -e ".[dev]"
pytest
IDENTIDAD_URL=http://localhost:5003 JWT_ISSUER=http://localhost:5003 \
  flask --app bff_web.app run --port 5001
```
