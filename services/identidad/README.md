# Identidad

Contexto delimitado de **Identidad y Cliente**. Es dueño de los usuarios y de las credenciales, y
el único que emite tokens: un access token JWT RS256 de 1 hora y un refresh token opaco que rota.
Los BFF delegan aquí el registro y el login, y el API Gateway valida los JWT con el JWKS que
publica. Contrato: `contracts/openapi/identidad-v1.yaml`.

## Endpoints

| Método | Ruta                                | Descripción                                   |
| ------ | ----------------------------------- | --------------------------------------------- |
| `POST` | `/v1/usuarios`                      | Registra un usuario cliente                   |
| `POST` | `/oauth/token`                      | `grant_type=password` o `refresh_token`       |
| `POST` | `/oauth/revoke`                     | Revoca un refresh token y su familia          |
| `GET`  | `/.well-known/openid-configuration` | Metadatos del emisor (sin autenticación)      |
| `GET`  | `/.well-known/jwks.json`            | Clave pública de firma (sin autenticación)    |
| `GET`  | `/ping`                             | Disponibilidad                                |

`/oauth/token` y `/oauth/revoke` aceptan `application/json` y
`application/x-www-form-urlencoded`. `client_id` (`web` o `movil`) es obligatorio.

```bash
curl -s localhost:8080/oauth/token -H 'Content-Type: application/json' \
  -d '{"grant_type":"password","client_id":"web","username":"ana@solventa.co","password":"..."}'
```

La respuesta incluye `access_token`, `token_type: Bearer`, `expires_in: 3600`, `refresh_token`,
`refresh_expires_in`, `scope: cliente` y `usuario`. Los errores son siempre
`application/problem+json` con `type`, `title`, `status`, `detail`, `code` y `correlationId`.

## Tokens

- **Access token:** JWT `RS256` con `kid` en la cabecera. Claims: `iss`, `sub`, `aud`, `iat`,
  `exp` (`iat` + 3600 s), `jti`, `scope`, `rol`, `nombre`, `correo` y `client_id`.
- **Refresh token:** `secrets.token_urlsafe(48)`; solo se guarda su SHA-256. Vive 8 horas para
  `web` y 30 días para `movil`.
- **Rotación:** cada uso válido revoca el token presentado y emite uno nuevo en la misma familia.
  La revocación es un `UPDATE ... WHERE revocado_en IS NULL` y se comprueba `rowcount`, así que
  de dos usos simultáneos solo uno gana.
- **Detección de reuso:** presentar un refresh token ya rotado revoca toda la familia y responde
  401 `refresh_invalido`. Esto incluye el caso de dos usos simultáneos legítimos del mismo token:
  el cliente debe volver a iniciar sesión.
- Un refresh token presentado con otro `client_id` es inválido y **no** revoca la familia.
- La vida del refresh es deslizante: cada rotación recibe de nuevo el TTL completo del cliente.

## Seguridad

- Contraseñas con PBKDF2-SHA256 (600.000 iteraciones, sal por contraseña); nunca en texto plano.
- Usuario inexistente y contraseña errónea responden igual, y en ambos casos se verifica un hash
  para no filtrar la existencia por tiempo de respuesta.
- Límite de intentos por correo (`login:<correo>` y `registro:<correo>`), sin incluir la IP, que
  detrás del gateway y el ALB no es fiable. Con `REDIS_URL` se comparte entre réplicas.
- Sin cookies, sin CORS (lo gestiona el API Gateway) y sin sesiones en el servidor.
- La clave privada llega por variable de entorno (Secrets Manager en AWS); el `kid` es el
  SHA-256 de la clave pública DER. Para rotar la clave hay que publicar ambas en el JWKS durante
  la transición; hoy el JWKS publica solo la vigente.

## Configuración

| Variable                      | Default        | Descripción                                          |
| ----------------------------- | -------------- | ---------------------------------------------------- |
| `DATABASE_URL`                | **obligatoria**| PostgreSQL (SQLite en pruebas)                       |
| `JWT_PRIVATE_KEY`             | **obligatoria**| PEM RSA; admite `\n` escapados                       |
| `JWT_ISSUER`                  | **obligatoria**| URL pública del emisor, sin `/` final                |
| `JWT_AUDIENCE`                | `solventa-api` | Claim `aud`                                          |
| `ACCESS_TOKEN_MINUTES`        | `60`           | Vida del access token                                |
| `REFRESH_TOKEN_HOURS_WEB`     | `8`            | Vida del refresh de `web`                            |
| `REFRESH_TOKEN_DAYS_MOVIL`    | `30`           | Vida del refresh de `movil`                          |
| `REDIS_URL`                   | memoria        | `rediss://:token@host:6379/0` para el límite         |
| `DB_SCHEMA`                   | `public`       | Esquema propio del dominio (PostgreSQL)              |
| `AUTH_MAX_ATTEMPTS`           | `5`            | Intentos por ventana y correo                        |
| `AUTH_ATTEMPT_WINDOW_SECONDS` | `300`          | Ventana de intentos                                  |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | `5` / `5` | Pool de conexiones                                   |

El servicio no arranca si falta alguna de las obligatorias.

## Base de datos

La migracion `0002` agrega `consentimientos_perfilamiento` y `outbox_consentimientos`;
`0003` agrega intentos, ultimo error y acuse durable de entrega. Cada decision y su
evento se guardan en una unica transaccion. La clave cliente/version impide que dos
decisiones concurrentes sobrescriban evidencia: una gana y la otra recibe 409.

El endpoint interno `GET/PUT /v1/clientes/{uuid}/consentimiento-perfilamiento` exige
`INTERNAL_API_TOKEN` como Bearer. Solo el BFF recibe clientes publicos; deriva el
titular y actor del JWT, nunca del formulario. PUT recibe `autorizado`, la `version`
actual (0 inicialmente) y, para aprobar o rechazar, una `solicitud` completa:
`finalidad=cotizacion`, `alcances`, `fuentes`, `datos=[indicador_riesgo]`,
`versionTerminos=1.0` y `vigenteHasta` RFC3339 con zona horaria, futura y hasta 30 dias.
Alcances/fuentes solo admiten `open_finance` y `open_data`, sin duplicados, y cada
fuente debe estar cubierta por el alcance. **Ambas fuentes son simuladas**.

Con solicitud, `autorizado=true` registra aprobacion y `false` registra rechazo.
Sin solicitud, `false` solo revoca una autorizacion existente conservando su alcance;
`true` devuelve 400. Fecha efectiva y actor los fija el servidor. GET no renueva la
vigencia: devuelve `vencido` al alcanzar el limite, sin mutar el historial original.
Los registros legados sin evidencia completa se conservan pero no autorizan al PEP.
Un alcance parcial no se amplia: el modelo de perfil actual exige las dos fuentes y
deniega su generacion si falta alguna.

### Outbox de consentimiento (SOL-326)

`python -m identidad.consent_outbox` entrega hasta 50 eventos por ejecucion al endpoint
interno `/v1/eventos/consentimientos` de Perfilamiento. Requiere `DATABASE_URL`,
`DB_SCHEMA` cuando corresponda, `INTERNAL_API_TOKEN` y `PERFILAMIENTO_SERVICE_URL`.
No usa JWT de clientes ni conecta con Open Finance. El contrato de transporte es
`contracts/events/consentimiento-v1.json`; la evidencia completa permanece en Identidad.

Prioriza revocaciones/rechazos, luego fecha efectiva. En PostgreSQL bloquea cada fila
con `FOR UPDATE SKIP LOCKED` durante el envio; revalida el limite de intentos bajo ese
bloqueo. La entrega es **al menos una vez**: si se pierde el acuse, el consumidor
idempotente devuelve el original sin duplicar su auditoria. Solo se marca publicado
con eventoId coincidente, version aplicada no inferior y fecha de aplicacion valida,
posterior o igual a la efectiva y no futura. Requiere relojes sincronizados.

Tras cinco intentos fallidos, la fila queda en DLQ logica (`publicado=false`,
`intentos>=5`, `ultimo_error`). La ejecucion falla visiblemente mientras haya DLQ.
Corregir conectividad/credenciales/consumidor antes de ejecutar
`python -m identidad.consent_outbox --reintentar-fallidos`; no editar ni eliminar
eventos. El comando tambien atiende pendientes normales y no reinicia contadores.

El CronJob corre cada minuto, sin solapamiento, con deadline de 50 segundos.
Las consultas autoritativas deniegan desde la revocacion persistida, sin esperar
al worker. El objetivo de adopcion del evento es <=5 minutos: verificar
`acuse.aplicadaEn - contenido.efectivaEn` y vigilar pendientes, DLQ y Jobs fallidos.
La capacidad de 50 eventos/tick y una caida del consumidor pueden incumplir ese
objetivo; el horario no constituye una garantia de produccion ni hay alarmas
automaticas nuevas. Ver el [orden de rollout](../../deploy/k8s/identidad/README.md#sol-326-consentimiento-y-outbox).

Tablas `usuarios` y `refresh_tokens`. Con `DB_SCHEMA` el motor fija `search_path` a ese esquema y
la migración lo crea (`CREATE SCHEMA IF NOT EXISTS`); la versión se guarda en
`alembic_version_identidad` dentro del mismo esquema.

```bash
DATABASE_URL=postgresql+psycopg://... DB_SCHEMA=identidad alembic upgrade head
```

La migración solo necesita `DATABASE_URL` y `DB_SCHEMA`: no requiere la clave JWT.

## Ejecución

```bash
pip install -e ".[dev]"
pytest
openssl genrsa 2048 > /tmp/identidad.pem
export DATABASE_URL=sqlite:///local.db JWT_ISSUER=http://localhost:8080
export JWT_PRIVATE_KEY="$(cat /tmp/identidad.pem)"
alembic upgrade head
flask --app identidad.app run --port 8080
```
