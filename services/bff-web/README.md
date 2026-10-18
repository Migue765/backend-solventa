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

Las rutas `GET /v1/solicitudes-cotizacion/{id}/ofertas` y
`GET/PUT /v1/solicitudes-cotizacion/{id}/seleccion` también requieren Bearer.
Comparan ofertas simuladas y consultan o guardan una selección idempotente.

Las solicitudes aceptan exclusivamente `{productoId, datosProducto}`; los datos de identidad
y contacto proceden del usuario autenticado, nunca del body. Se admiten `automotive`,
`travel`, `life`, `device-protection`, `parametric`, `parametric-weather`,
`payment-protection` y `mortgage-life`, con validación estricta por producto.
Los campos y límites están definidos en el contrato OpenAPI y en
`services/cotizacion/README.md`. `parametric.coverageType` es opcional y conserva la
compatibilidad de registros anteriores; las fechas futuras se exigen al crear, no al leer.
Las ofertas son simuladas y mantienen `{monto, moneda: "COP"}`: no hay integraciones
meteorológicas, consultas a aerolíneas, emisión ni pagos reales.

Los errores usan `application/problem+json` con `code` y `correlationId`. Si identidad o su JWKS
no responden, el BFF contesta `503 servicio_no_disponible` (nunca `401`), para que el cliente no
cierre una sesión válida por una caída. Un `429 demasiados_intentos` conserva `Retry-After`.
`X-Correlation-ID` se propaga a identidad.

### Importes y periodicidad de ofertas

Las ofertas y GET/PUT de selección transmiten `prima: {monto, moneda}` y
`periodicidad: mensual | por_viaje | por_vuelo | por_periodo` calculadas y persistidas por cotización.
El BFF no calcula precios ni transforma documentos de identidad en riesgo.
`primaMensual` mantiene el importe solo para `mensual`; es `null` para viaje/vuelo/período asegurado.
Los consumidores deben leer `prima` y usar la periodicidad para la etiqueta del importe:
**el contrato no es retrocompatible con clientes que asumen `primaMensual` no nulo**.
El adaptador acepta respuestas antiguas como mensuales únicamente si faltan ambos campos nuevos.
La selección sigue recibiendo `{ofertaId, version}` con `Idempotency-Key`.
En clima, `por_periodo` identifica el importe completo de la ventana inclusiva
`datosProducto.startDate` a `endDate`; la oferta muestra ese intervalo en `vigencia`.
`vigenteHasta` sigue siendo el plazo de aceptación de la cotización, no la ventana de cobertura.

### Cómo usa los tokens el frontend

El access token vive solo en memoria; el refresh token, en `sessionStorage`. Cuando el access
token está por vencer (`expiraEn`) o responde 401, el cliente llama a `/v1/sesiones/renovar` y
reemplaza ambos tokens: el refresh anterior deja de servir.

### Consentimiento y perfilamiento

`GET/PUT /v1/consentimientos/perfilamiento` consulta, autoriza o revoca el
consentimiento real de Identidad. El cuerpo de PUT es `{autorizado, version}`:
la version inicial es 0; una version desactualizada responde 409. No se autoriza
automaticamente ni se acepta un cliente desde el formulario; siempre procede del JWT.
La autorizacion dura 30 dias. `INTERNAL_API_TOKEN` autentica estas llamadas internas.

`GET /v1/solicitudes-cotizacion/{id}/perfilamiento` recupera el mismo perfil
persistido para el calculo, con vigencia maxima de 15 minutos y ambas fuentes
Open Finance/Open Data rotuladas como simuladas. Revocar, renovar la autorizacion
o vencer el perfil bloquea su reutilizacion, las ofertas y la seleccion.
Una solicitud anterior sin evidencia devuelve `perfil_no_encontrado`; se debe crear
una nueva despues de autorizar. No se inventan perfiles para solicitudes antiguas.

En AWS deben desplegarse Identidad (migracion 0002), Perfilamiento, Cotizacion
con `COTIZACION_PERFILAMIENTO_HABILITADO=true` y el BFF. La regla ALB
`bff_web_consents` lleva la consulta de consentimiento al BFF y CORS expone `Date`
para validar la vigencia con el reloj del servidor.

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

### Integración local en PowerShell

Desde la raíz del backend, preparar un entorno local con los tres servicios:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".\services\identidad" -e ".\services\cotizacion" -e ".\services\bff-web"
```

Abrir tres consolas en esa misma raíz. Los puertos siguientes son locales de ejemplo
y deben estar libres; no es necesario Redis para esta comprobación.

Identidad (clave RSA nueva solo en memoria; no usar claves de producción):

```powershell
$env:PYTHONPATH = (Resolve-Path services\identidad\src).Path
$env:DATABASE_URL = "sqlite:///identidad-local.db"
$env:DB_SCHEMA = ""
$env:REDIS_URL = ""
$env:JWT_ISSUER = "http://127.0.0.1:15003"
$env:JWT_PRIVATE_KEY = & .\.venv\Scripts\python -c "from cryptography.hazmat.primitives.asymmetric import rsa; from cryptography.hazmat.primitives import serialization as s; print(rsa.generate_private_key(public_exponent=65537,key_size=2048).private_bytes(s.Encoding.PEM,s.PrivateFormat.PKCS8,s.NoEncryption()).decode())"
.\.venv\Scripts\python -m alembic -c services\identidad\alembic.ini upgrade head
.\.venv\Scripts\python -m flask --app identidad.app run --host 127.0.0.1 --port 15003
```

Cotización:

```powershell
$env:PYTHONPATH = (Resolve-Path services\cotizacion\src).Path
$env:DATABASE_URL = "sqlite:///cotizacion-local.db"
.\.venv\Scripts\python -m alembic -c services\cotizacion\alembic.ini upgrade head
.\.venv\Scripts\python -m flask --app cotizacion.app run --host 127.0.0.1 --port 15000
```

BFF Web:

```powershell
$env:PYTHONPATH = (Resolve-Path services\bff-web\src).Path
$env:IDENTIDAD_URL = "http://127.0.0.1:15003"
$env:JWT_ISSUER = "http://127.0.0.1:15003"
$env:QUOTE_SERVICE_URL = "http://127.0.0.1:15000"
.\.venv\Scripts\python -m flask --app bff_web.app run --host 127.0.0.1 --port 15001
```

Comprobar `/ping` en cada puerto. El proxy de desarrollo web debe dirigir `/api` al BFF
`http://127.0.0.1:15001`, eliminando el prefijo `/api`; el BFF atiende `/v1/...`.
No se usa `AUTH_DEVELOPMENT_USERS_JSON`, `passwordHash` ni autenticación por cookie.

Para una prueba sin credenciales compartidas, generar una contraseña aleatoria en el cliente
sin imprimirla. Registrar con `POST /v1/usuarios` y `{nombre, correo, contrasena}`; iniciar sesión
con `POST /v1/sesiones` y `{correo, contrasena}`. La respuesta es
`{usuario, tokenType, accessToken, refreshToken, expiraEn, refreshExpiraEn}`.
Enviar el `accessToken` como Bearer en las consultas y cotizaciones. `GET /v1/sesiones/actual`
solo devuelve `{usuario, expiraEn}`; no recupera tokens. Renovar con
`POST /v1/sesiones/renovar` y `{refreshToken}`. Cerrar con
`DELETE /v1/sesiones/actual`, Bearer y `{refreshToken}` para revocarlo: responde `204`.
No guardar contraseñas, claves privadas ni tokens en archivos versionados o logs.
