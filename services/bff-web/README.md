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

### Plataforma de socios (SOL-195)

El rol `socio` es distinto de `asesor` y `cliente`. Usa el mismo login, refresh y
JWT del canal web, con `scope: socio` y claim `socios` obtenido de Identidad.
Login y `/v1/sesiones/actual` incluyen `usuario.socios` para estos operadores.
`GET /v1/socios/actual` devuelve `apiVersion: "1"`, `usuario`, `socios` (cada
organizacion con `socioId` y los IDs de productos disponibles del catalogo) y
`alcances: ["perfilamientos:crear", "clientes:consultar"]`. No hay registro publico de socios.

`POST /v1/socios/consultas-informacion-cliente` implementa HU44. Requiere
`Idempotency-Key` igual a `operationId`, toma el actor del JWT y valida la pertenencia
al socio, el cliente registrado y el producto disponible antes de delegar a
Perfilamiento. Solo solicita y devuelve datos cubiertos por el consentimiento
autoritativo vigente. Un fallo o circuito abierto sin cache integra, vigente y autorizada
responde `503` sin datos parciales ni defaults. La respuesta exitosa incluye la versión del
consentimiento y evidencia por fuente, y usa `Cache-Control: no-store`.
`GET /v1/socios/consultas-informacion-cliente/{operationId}?socioId=...` recupera
la consulta persistida sin repetir fuentes y vuelve a comprobar que el consentimiento
exacto siga vigente antes de entregar los datos.

Configurar `PERFILAMIENTO_SERVICE_URL` con la URL interna de Perfilamiento. El BFF
limita esta dependencia a 800 ms.

El bloqueo acreditado de `open_finance` en HU44 devuelve problem+json
`503 consulta_bloqueada` con `operacion`: `operationId`, `socioId`, `clienteId`,
`productoId`, `excepcionId`, `estadoDecision` y `aplicacion`. No tratar otros 503,
HU48/hipotecarios, fallos de open_data o consentimiento como excepciones revisables.
`GET /v1/socios/consultas-informacion-cliente/{operationId}/estado?socioId=...` recupera
esa misma proyeccion con permisos y consentimiento actuales; no consulta fuentes ni
devuelve datos financieros. Una operacion no vinculada devuelve `404 consulta_no_encontrada`.
La respuesta acredita recepcion HTTP del solicitante; el `acuseId` por si solo acredita
aplicacion durable, no recepcion. Si se completo, el GET existente recupera la consulta
persistida sin duplicarla. Repetir el POST rechazado devuelve `409 operacion_rechazada`
con la proyeccion, siempre que el consentimiento siga vigente.

`POST /v1/socios/solicitudes-cotizacion` integra `mortgage-life` con el motor y la
persistencia principales, no una simulacion paralela:

```json
{
  "operationId": "11111111-1111-4111-8111-111111111111",
  "socioId": "BANCO-001",
  "productoId": "mortgage-life",
  "datosProducto": {
    "mortgageBalance": "310000000",
    "referenciaCredito": "HIP-2026-001"
  },
  "cliente": {
    "tipoCliente": "persona_natural",
    "tipoDocumento": "cc",
    "numeroDocumento": "1023456789"
  }
}
```

Exige `Idempotency-Key` igual a `operationId`. `API-Version: 1` es opcional;
otra version devuelve `400 version_no_soportada`. La referencia de credito identifica
el credito real durante perfilamiento y cotizacion; nunca se reemplaza por `operationId`.
El BFF resuelve el cliente por documento en Identidad y toma de forma autoritativa correo,
telefono, fecha de nacimiento y consentimiento. No se publica un catalogo global de
clientes a socios. La pertenencia del operador al socio se comprueba antes de consultar
dependencias.

Respuesta `201`: `{operationId,socioId,clienteId,productoId,estado:"completada",
perfilamientoReferencia,solicitudId,cotizacionId,recibidaEn}`.
`perfilamientoReferencia` es una referencia opaca; `cotizacionId` es UUID.
La solicitud queda disponible en el flujo habitual del **cliente titular**.
El consentimiento debe haber sido otorgado por ese cliente mediante SOL-203;
el socio no puede otorgarlo por el cliente. Ausente/revocado/vencido/version
incorrecta rechaza la operacion antes de consultar fuentes.

Repetir POST con mismo UUID y cuerpo recupera la misma operacion, incluso entre
replicas. `GET /v1/socios/solicitudes-cotizacion/{operationId}?socioId=...`
recupera el mismo resultado sin repetir perfilamiento ni necesitar datos personales.
Devuelve `404 operacion_no_encontrada`, `409 operacion_en_curso` o
`409 operacion_reintentable` si no hay resultado. GET nunca reanuda;
el ultimo caso necesita POST original. `409 idempotencia_conflicto` impide
reutilizar la clave con otros datos. La admision generica garantiza UUID global
unico incluso entre organizaciones: una colision devuelve409 sin revelar el socio
propietario. Reintentos y lecturas siguen aislados por organizacion.

`POST /v1/socios/solicitudes-cotizacion/{operationId}/oferta?socioId=...` (SOL-330)
pide a Cotizacion la oferta explicable de una operacion completada: tres planes con
precio, coberturas, condiciones y factores, mas riesgo, fuentes, variables y versiones.
Exige el mismo JWT de socio y `socioId` en el claim `socios`; responde `201` incluso en
reintentos, que devuelven la oferta persistida. `GET` sobre la misma ruta solo la lee.
Entradas vencidas responden `422 entradas_vencidas`; una decision inconsistente,
`422 oferta_no_disponible`; nunca se devuelve una oferta parcial.

Errores previos a perfilamiento: `400 solicitud_invalida`, `401 sesion_invalida`,
`403 alcance_insuficiente`/`socio_no_autorizado`, `404 cliente_no_encontrado`.
Admision persistente de Cotizacion devuelve `429 cuota_socio_excedida` o
`socio_ocupado` con `Retry-After`; un duplicado en curso da `409` con ese header.
Fallos de dependencias devuelven `503`, nunca una cotizacion artificial.
Respuestas llevan `Cache-Control: no-store` y `X-Correlation-ID`.

Los rechazos 400/401/403/409/429 de socios y asesor generan un registro WARNING
`sol325.rechazo` con campos estructurados `event`, `code`, `reason`, `status`,
`correlationId` y `endpoint`, tanto en el mensaje JSON como en `extra` del logging.
`code` conserva el codigo publico; `reason` distingue
credencial ausente, invalida (incluida vencida) y alcance insuficiente, incluso
cuando asesor conserva su respuesta 401. `actorId` solo se incluye tras validar
JWT, rol y alcance; `socioId`, despues de comprobar la pertenencia. No se registra
el socio rechazado del cuerpo o query, tokens, cabeceras, datos del cliente ni
errores completos. Cotizacion registra el mismo evento para rechazos de admision,
sin atribuir actor o socio a partir del cuerpo interno. Ambos registros se enlazan
por `correlationId`; no constituyen dos operaciones ni cambian `Retry-After`.
Estos campos usan el logging existente; su almacenamiento y retencion dependen
del colector de logs del entorno.

El mismo evento cubre los rechazos 400/401/403/409 de
`POST /v1/socios/consultas-informacion-cliente` (HU44), incluido el conflicto
idempotente y el consentimiento denegado. Su `socioId` solo se atribuye despues de
validar pertenencia; la auditoria por fuente de Perfilamiento permanece separada.
Esto no agrega cuota de consultas ni cambia su replay, circuit breaker o contrato.
Los rechazos previos del Gateway no pasan necesariamente por este hook del BFF.

La contraparte de HU42-BE/SOL-325 es HU42-FE/SOL-194: el portal consume
`GET /v1/socios/actual` con `API-Version: 1` y la sesion autenticada, no un socio
fijo ni un contexto enviado por el navegador. `usuario` coincide con
`/v1/sesiones/actual`; las organizaciones salen del JWT verificado. Cambiar un
`socioId` en la query no cambia ese contexto. La lectura no consume la cuota de
creacion: agotar esa cuota no impide volver al portal.

`tests/test_partner_flow.py -k "not latency"` cubre por HTTP local el contrato
del contexto para dos socios, alternancia de sesiones, credencial ausente,
invalida o vencida, rol no autorizado y version incompatible, ademas de los
rechazos auditables, cuota, aislamiento e idempotencia. Usa JWT reales emitidos
por Identidad y bases temporales; no verifica el navegador ni retencion de logs.
Que el portal FE este listo no acredita por si solo el cierre de SOL-325 ni
aislamiento fisico de CPU entre organizaciones.

HU46 conserva `/v1/perfilamientos-hipotecarios` y `/v1/asesor/clientes` para asesor.
Para socio, vida hipotecaria se cotiza en la ruta generica usando
`productoId: "mortgage-life"`. El navegador solo envia documento y saldo hipotecario;
Identidad aporta telefono y fecha de nacimiento maestros, y el BFF resuelve UUID y
consentimiento. La respuesta incluye la oferta recomendada calculada por Cotizacion.
Para HU49, esa oferta se recupera por `operationId` sin recalcular y añade
`explicacion.factoresDeterminantes` y `trazabilidad` con las versiones del perfil,
fuentes y regla. El BFF valida estrictamente esa estructura, pero no recibe ni expone
la evidencia completa usada para reconstruir la decisión, que permanece en Cotizacion.

La gestión de excepciones Open Finance está en `/v1/asesor/excepciones-open-finance`.
Requiere JWT verificado con `rol=asesor`, `scope=asesor` y el `socioId` de la query
presente en el claim `socios`; el actor de la decisión siempre es el `sub` del token.
`GET /v1/asesor/excepciones-open-finance?socioId=...` lista únicamente pendientes;
`GET /v1/asesor/excepciones-open-finance/{excepcionId}?socioId=...` expone causa,
consentimiento asociado y trazabilidad mínima. `POST
/v1/asesor/excepciones-open-finance/{excepcionId}/resolver?socioId=...` acepta
`{decision:"aprobar"|"rechazar",motivo}`
(3–500 caracteres) y confirma únicamente después de persistir la transición y su evento.
La aprobación revalida el consentimiento vigente e idéntico; si falta o cambió, devuelve
403 `consentimiento_ausente` o `consentimiento_no_vigente` sin resolver el caso. La
decisión nunca concede ni amplía fuentes, datos o finalidad; el proceso original debe
revalidar el consentimiento antes de continuar. `409 excepcion_ya_resuelta` indica que
la bandeja ya fue atendida, y un caso de otro socio se oculta con 404 en detalle/decisión.
La regla ALB `bff_web_partners` (prioridad 13) lleva estas rutas al BFF; CORS
permite y expone `API-Version`. No desplegar solo Cotizacion: la ruta publica
siempre debe pasar por autenticacion del BFF.
El preflight CORS lo responde API Gateway, no Flask. El contrato versionado permite
`GET`/`OPTIONS` y los headers `Authorization`, `API-Version` y `X-Correlation-ID`;
expone `API-Version`, `X-Correlation-ID` y `Retry-After`. La prueba local comprueba
esa configuracion declarada, no los origenes ni el preflight del entorno desplegado.

### SOL-331: decision, aplicacion y recuperacion HU44

`estado` de la excepcion sigue representando solo la decision humana. Mientras esta
pendiente, `decision`, `actorId`, `resueltaEn` y `motivo` se serializan **null**, no se
omiten. `proceso` y `aplicacion` son null en casos historicos sin operacion demostrable;
no se habilita reanudacion mediante un enlace inventado. El contrato completo y su
nullabilidad estan en `contracts/openapi/bff-web-v1.yaml`.

Un caso vinculado agrega `proceso={tipo:"consulta_informacion_cliente",
actorSolicitanteId,productoId,finalidad,datosSolicitados}` y
`aplicacion={estado,intentos,codigo,actualizadaEn,acuseId}`. Estados:
`esperando_decision`, `pendiente`, `completada`, `bloqueada`, `detenida`.
`intentos` es 0 o 1; `codigo`/`acuseId` son null antes de aplicar. Completar da
`consulta_disponible`; rechazar da `operacion_rechazada`; bloquear conserva la causa
(por ejemplo `fuente_no_disponible`, `consentimiento_no_vigente`,
`actor_no_autorizado`, `producto_no_autorizado` o `catalogo_no_disponible`).

Resolver persiste primero la decision y luego despacha su aplicacion. Por eso un 503
no permite inferir que la decision fallo: recuperar el detalle. El GET de bandeja admite
`estadoAplicacion=pendiente|bloqueada|completada|detenida`; asi un caso decidido no desaparece
del flujo normal al recargar. Sin filtro siguen apareciendo solo decisiones pendientes.
El cliente no debe volver a decidir para recuperar la aplicacion.

`POST /v1/asesor/excepciones-open-finance/{id}/aplicar?socioId=...` requiere JSON `{}`,
JWT asesor y pertenencia al socio. Retorna 200 con **todo el caso**, no un exito financiero:
leer decision y aplicacion por separado. Revalida permisos actuales; una aprobacion
revalida tambien solicitante, catalogo y consentimiento original. Sin respuesta valida
queda bloqueada con acuse, terminal y **sin boton de reaplicar**. Rechazar detiene la
operacion sin leer fuentes. El mismo acuse se devuelve en duplicados sin repetir efectos.
Solo `pendiente` sin acuse se puede reentregar tras interrupcion; no hay worker.

Errores controlados: 400 `solicitud_invalida`/`version_no_soportada`; 401 por sesion/rol
invalido; 403 `socio_no_autorizado`/`actor_no_autorizado`; 404 `excepcion_no_encontrada`;
409 `decision_pendiente`/`excepcion_sin_operacion`; 503 `servicio_no_disponible`.
Resolver conserva ademas los rechazos de consentimiento y `excepcion_ya_resuelta`.
Solo en casos vinculados, repetir la misma decision/actor/motivo normalizado es idempotente.
El GET de estado HU44 usa `403 consentimiento_invalido` para consentimiento ausente/cambiado.
Todos los errores controlados conservan problem+json, correlacion y no-store.

Publicar las dependencias compatibles y ejecutar `init-db` de Perfilamiento antes de
habilitar la UI. No admitir replicas antiguas que ignoren decisiones/aplicaciones; conservar
el ledger no hace seguro un rollback a codigo anterior. Estas instrucciones no autorizan
operaciones de despliegue ni acreditan la validacion de la UI/AWS.

Validacion local: `python -m pytest tests/test_partner_flow.py` desde la raiz.
`RUN_PARTNER_BENCHMARK=1` habilita 100 operaciones de vida hipotecaria por HTTP local
entre servicios/SQLite con umbrales p95 250ms y p99 500ms. No acredita SLA AWS:
medir tambien el gateway publico regional, con la ubicacion del generador,
100 o mas muestras por producto, errores, cuotas y concurrencia entre socios.
### Login cifrado (deshabilitado por defecto)

Con `ENCRYPTED_LOGIN_ENABLED=true` en **BFF e identidad**, `GET /v1/sesiones/desafio`
es público y devuelve `{desafioId, clavePublica, expiraEn}` de identidad, sin caché.
`POST /v1/sesiones` acepta exclusivamente `{correo, credencialCifrada}` y rechaza
`contrasena` y campos adicionales. El BFF no descifra: reenvía el sobre a
`POST /oauth/token` con `grant_type=encrypted_password`, `client_id=web`,
`username=correo`, `credencialCifrada`. No hay Redis ni claves privadas en el BFF.
El cliente debe generar JWE `RSA-OAEP-256` + `A256GCM` y pedir un desafío nuevo
por intento. Véase [identidad](../identidad/README.md#login-web-cifrado-opt-in-no-desplegado).

Sin el flag, el login heredado sigue funcionando y el desafío responde 503.
No hay fallback entre contratos. Refresh, logout, móvil y **registro** no cambian;
registro aún envía su contraseña sin este sobre. HTTPS es imprescindible:
esto no oculta la contraseña al dueño del navegador ni frente a XSS.

La excepción pública `GET /v1/sesiones/desafio` está declarada con throttling de
autenticación. La ruta interna de identidad no se publica en API Gateway.
Ver [operación coordinada](../../docs/operacion-login-cifrado.md) antes de habilitar flags.

| Método   | Ruta                              | Auth   | Descripción                                   |
| -------- | --------------------------------- | ------ | --------------------------------------------- |
| `POST`   | `/v1/usuarios`                    | No     | Registra un usuario cliente (proxy a identidad) |
| `POST`   | `/v1/sesiones`                    | No     | Inicia sesión: access token + refresh token   |
| `GET`    | `/v1/sesiones/desafio`            | No     | Desafío cifrado de un solo uso (opt-in)       |
| `POST`   | `/v1/sesiones/renovar`            | No     | Rota el refresh token y emite nuevos tokens   |
| `GET`    | `/v1/sesiones/actual`             | Bearer | Usuario y vencimiento tomados del JWT         |
| `DELETE` | `/v1/sesiones/actual`             | Bearer | Cierra sesión; revoca el `refreshToken` si se envía |
| `GET`    | `/v1/productos-seguro`            | Bearer | Lista los seguros disponibles                 |
| `POST`   | `/v1/solicitudes-cotizacion`      | Bearer | Registra una solicitud (`Idempotency-Key`)    |
| `GET`    | `/v1/solicitudes-cotizacion/{id}` | Bearer | Consulta una solicitud                        |
| `GET`    | `/ping`                           | No     | Comprueba disponibilidad                      |

Las rutas `GET /v1/solicitudes-cotizacion/{id}/ofertas` y
`GET/PUT /v1/solicitudes-cotizacion/{id}/seleccion` también requieren Bearer.
Comparan ofertas simuladas y consultan o registran la aceptación explícita e idempotente.
El PUT histórico no es una selección provisional y no debe adelantarse a HU14.

La nueva ruta `GET/PUT /v1/solicitudes-cotizacion/{id}/seleccion-provisional`
requiere el mismo Bearer y controles de titularidad/perfil; no concede consentimiento.
PUT recibe `{ofertaId, version, revisionEsperada}` e `Idempotency-Key` UUID.
La revisión inicial es `0`; cada cambio confirmado devuelve el DTO de selección
actual más `revision` y `vigenteHasta`. No marca la oferta como aceptada ni crea
una suscripción. GET devuelve
`{solicitudId, seleccion: <snapshot>|null, aceptacion: <DTO histórico>|null, estadoOferta}`
y es de solo lectura/no-store. La aceptación existente prevalece al reanudar.

Cada elección nueva usa una clave nueva; su reintento conserva clave y payload.
Repetir una clave antigua recupera su snapshot, nunca reactiva esa elección.
La UI debe consultar GET antes de confirmar HU14; si cambió la oferta o revisión
mostrada, debe retirar el checkbox/confirmación y presentar el resumen nuevo.
Entonces PUT `/seleccion` envía `{ofertaId, version, revisionSeleccion}` y conserva
su respuesta histórica. Solo después de aceptación exitosa se permite POST
`/suscripcion`. Regresar/error no debe dispararlo.

La compatibilidad legacy sin `revisionSeleccion` se mantiene **solo si no hay
provisional**. Una operación con provisional exige su revisión incluso desde un
cliente antiguo. No habilitar el nuevo recorrido entre réplicas backend de versiones
mezcladas: migrar y actualizar backend antes del frontend.

Errores identificables: `409 seleccion_desactualizada` (revisión/referencia incorrecta
o ausente), `409 seleccion_no_modificable` (nueva provisional tras aceptación),
`409 conflicto_idempotencia` (misma clave/otros datos), `422 oferta_no_disponible`
(oferta vencida/retirada/inexistente o intento de reemplazar aceptación histórica).
Los replays válidos conservan el acuse, sin mutar; no son permiso para aceptar
una oferta que ya perdió vigencia. Se conservan 401, 403, 404 y 503 del flujo actual.

### Tablero real del cliente

`GET /v1/clientes/me/dashboard` requiere JWT y no acepta query ni cuerpo: el cliente
procede exclusivamente del principal autenticado. El gateway consulta
`GET /v1/dashboard?clienteId=<principal>` en Cotizacion, autenticado con el
`INTERNAL_API_TOKEN` existente en ambos servicios. Sin esa configuracion o si falla
Cotizacion, la base o la validacion del contrato, el BFF devuelve `503`, no un resumen vacio.
Credenciales JWT invalidas devuelven `401`; parametros o cuerpo devuelven `400`.
La respuesta satisfactoria tiene `Cache-Control: no-store`.

El contrato devuelve arrays reales `cotizaciones`, `suscripciones` y `pagos`.
Los importes son strings decimales de dos posiciones y las fechas incluyen zona horaria.
Cada cotizacion conserva ID, solicitud (null en registros antiguos), producto, estado,
fechas, prima estimada y seleccion opcional. La prima estimada no tiene periodicidad
persistida y **no es una prima mensual**. Seleccion y pago conservan su periodicidad
exacta (`mensual`, `por_viaje`, `por_vuelo`, `por_periodo`), sin recalculos ni totales
que mezclen monedas o periodicidades. Suscripcion conserva su estado y saga actuales;
el pago se identifica siempre como `simulada: true`, nunca como un cobro real.

Con `POLIZAS_SERVICE_URL` habilitado en Cotizacion, `polizas` es un array de
emisiones academicas persistidas por el nuevo servicio: `{id,suscripcionId,tipo,
fechaInicio,fechaFin,estado,simulada:true}`. Fechas `YYYY-MM-DD` inclusivas;
estado `programada`, `vigente` o `vencida` respecto del dia UTC. No acredita
cobertura real ni convierte pagos historicos en polizas. El BFF valida el contrato,
no calcula vigencias ni deriva polizas a partir de la saga.
Sin habilitar Polizas se conserva `{disponibilidad:"no_implementado"}`, nunca una
lista vacia artificial; el consumidor debe informar indisponibilidad.
`notificaciones` conserva ese marcador y no se genera desde eventos.
Una coleccion vacia disponible si representa una consulta sin registros.
La lectura historica no renueva ni exige perfiles de riesgo vigentes y
no modifica suscripciones, pagos o transacciones. No ofrece reanudacion con datos parciales.
El detalle de campos y tipos esta en `contracts/openapi/bff-web-v1.yaml`.

### Pago exclusivamente simulado

`GET/POST /v1/solicitudes-cotizacion/{id}/pago-simulado` requiere el mismo JWT.
POST exige `Idempotency-Key` UUID y cuerpo ausente o `{}`. El BFF no acepta importe,
moneda, cliente, tarjeta ni referencia bancaria; cotización toma el importe y la
periodicidad exactos de la selección aprobada persistida. Para mostrar el importe
antes de confirmar se consulta GET selección y se contrasta oferta/versión con
GET suscripción. No hay proveedor ni cobro real, firma o cobertura real.
Con Polizas habilitado, un pago nuevo confirmado genera una emision academica idempotente
antes de responder. Si esa emision falla, POST devuelve `503` aunque el pago
simulado ya este persistido: repetir POST en la misma solicitud completa el flujo
sin duplicarlo. GET pago es solo lectura y no emite.
Las reglas autorizadas y el despliegue estan en `services/polizas/README.md`.

Para el confirmado, ambos métodos devuelven `200` con `id`, `solicitudId`, `suscripcionId`, `ofertaId`,
`ofertaVersion`, `estado: confirmado`, `simulada: true`,
`referenciaInterna: SIM-<UUID>`, `importe: {monto, moneda}`, `periodicidad`,
`confirmadaEn` y la instantánea `saga: {estado: pago_confirmado, version, actualizadaEn}`.
GET suscripción refleja la transición persistida. Repetir POST devuelve el mismo
pago sin duplicar eventos, incluso tras vencer la oferta o avanzar posteriormente
la saga. La clave de creación no puede reutilizarse en otra solicitud (`409`).

El servidor de Cotizacion admite `PAGO_SIMULADO_RESULTADO=confirmado` (default) o
`rechazado`; el navegador no puede enviar ni sobrescribir ese escenario.
El rechazo definitivo es **HTTP 200, `estado: rechazado`**, no una excepcion:
conserva los campos compartidos, pero reemplaza `confirmadaEn` por `rechazadaEn`
y `motivo: rechazo_simulado`. Su `id`/`referenciaInterna` identifican el intento;
`saga` conserva `pago_pendiente` version 1. No se confirma pago ni se emite poliza.
El BFF rechaza como 503 respuestas de rechazo incoherentes o con `confirmadaEn`.

Repetir POST con la misma clave devuelve el resultado persistido aunque cambie
el escenario. Solo una nueva accion explicita tras rechazo definitivo debe usar
otra clave para un nuevo intento. No cambiar la clave por un timeout, 503,
recarga o recuperacion de emision. Si ya existe un confirmado, una clave nueva
recupera el mismo pago; una clave utilizada en otra solicitud produce 409.
GET pago y `dashboard.pagos` priorizan el confirmado; sin el devuelven el ultimo
rechazo, que en dashboard lleva `rechazadaEn`/`motivo` y no `confirmadaEn`.
Un replay de una clave rechazada antigua no cambia ese resultado actual.
La UI debe discriminar por `estado`, no interpretar todo HTTP 200 como pago exitoso.
No hay resultado financiero ambiguo/pendiente ni conciliador automatico.

GET sin resultado devuelve **404 `pago_no_encontrado` solo después de comprobar ownership**;
404 `solicitud_no_encontrada` significa ajena/inexistente y no debe habilitar pago.
POST sin suscripción devuelve 404; suscripción no aprobada, selección inválida,
cotización vencida o saga no pendiente devuelven 422 `pago_no_permitido`.
400 `solicitud_invalida`, 401 `sesion_invalida` y 503 `servicio_no_disponible`
conservan el manejo normal del canal. No convertir fallos de transporte en pago aprobado.

La ruta principal `/v1/solicitudes-cotizacion*` ya enruta al BFF. CORS existente
permite Pages, GET/POST y `authorization`, `content-type`, `idempotency-key`;
no se requiere otro API Gateway, microservicio ni cambio de infraestructura.

Las solicitudes aceptan exclusivamente `{productoId, datosProducto}`; los datos de identidad
y contacto proceden del usuario autenticado, nunca del body. Se admiten `automotive`,
`travel`, `life`, `device-protection`, `parametric`, `parametric-weather`,
`payment-protection` y `mortgage-life`, con validación estricta por producto.
Los campos y límites están definidos en el contrato OpenAPI y en
`services/cotizacion/README.md`. `parametric.coverageType` es opcional y conserva la
compatibilidad de registros anteriores; las fechas futuras se exigen al crear, no al leer.
Las ofertas son simuladas y mantienen `{monto, moneda: "COP"}`: no hay integraciones
meteorológicas, consultas a aerolíneas, emisión real ni pagos reales.

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

`GET/PUT /v1/consentimientos/perfilamiento` consulta, aprueba, rechaza o revoca el
consentimiento persistido en Identidad. La version inicial es 0; una version
desactualizada responde 409. El cliente y actor proceden exclusivamente del JWT.
`INTERNAL_API_TOKEN` autentica las llamadas del BFF a Identidad.

Para aprobar o rechazar, PUT recibe la decision explicita y la solicitud presentada
al usuario (sustituir la fecha de ejemplo por una futura, como maximo a 30 dias):

```json
{
  "autorizado": true,
  "version": 0,
  "solicitud": {
    "finalidad": "cotizacion",
    "alcances": ["open_finance", "open_data"],
    "fuentes": ["open_finance", "open_data"],
    "datos": ["indicador_riesgo"],
    "versionTerminos": "1.0",
    "vigenteHasta": "2026-10-25T23:59:00Z"
  }
}
```

`autorizado=false` con solicitud registra `rechazado`. Para revocar la autorizacion
actual, enviar solo `{"autorizado": false, "version": 1}`. La respuesta incluye
actor, fecha efectiva, alcance, fuentes, datos, version de terminos y vigencia.
GET devuelve `vencido` al alcanzar el plazo; nunca renueva ni autoriza implicitamente.

**Cambio incompatible de SOL-326:** una aprobacion con solo `{autorizado, version}`
ahora responde 400 `solicitud_invalida`. El BFF conserva ese error de Identidad.
Coordinar la pantalla y el payload con SOL203 antes de desplegar; este PR no implementa
la UI. Nunca completar permisos silenciosamente en el BFF ni migrar consentimientos
legados a aprobaciones completas. Los datos de Open Finance/Open Data son siempre
sinteticos; no hay conexiones a proveedores reales.

`GET /v1/solicitudes-cotizacion/{id}/perfilamiento` recupera el mismo perfil
persistido para el calculo, con vigencia maxima de 15 minutos y ambas fuentes
Open Finance/Open Data rotuladas como simuladas. Revocar, renovar la autorizacion
o vencer el perfil bloquea su reutilizacion, las ofertas y la seleccion.
Una solicitud anterior sin evidencia devuelve `perfil_no_encontrado`; se debe crear
una nueva despues de autorizar. No se inventan perfiles para solicitudes antiguas.

El [runbook SOL-326](../../deploy/k8s/identidad/README.md#sol-326-consentimiento-y-outbox)
coordina cliente compatible, Identidad (migracion 0003), nueva tabla de eventos en
Perfilamiento y Outbox. Cotizacion debe conservar
`COTIZACION_PERFILAMIENTO_HABILITADO=true`. La regla ALB
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
