# Perfilamiento HU11

Implementa SOL-163 con puertos de consentimiento y fuentes externas. Open Finance y Open Data
son mocks sinteticos: no consultan bancos, buros ni proveedores. El modelo `perfil-mock-1`
usa el mayor indicador de las dos fixtures; no es una regla actuarial aprobada.

```powershell
python -m pip install -e ".\services\perfilamiento[dev]"
$env:INTERNAL_API_TOKEN = "<mismo secreto de Identidad y Cotizacion>"
$env:IDENTIDAD_SERVICE_URL = "http://localhost:5002"
$env:PERFILAMIENTO_DATABASE_URL = "sqlite:///perfilamiento.db"
python -m flask --app perfilamiento.app init-db
python -m flask --app perfilamiento.app run --port 5003
```

API interna con Bearer; `/health` no requiere autenticacion:

| Metodo | Ruta | Entrada |
|---|---|---|
| POST | `/v1/perfiles` | `clienteId` y `referenciaId` UUID de la cotizacion |
| POST | `/v1/consultas-informacion-cliente` | Contexto autorizado de HU44 e identificador idempotente |
| GET | `/v1/perfiles/{perfilId}` | `clienteId` en query |
| POST | `/v1/eventos/consentimientos` | Evento interno del Outbox de Identidad |

El servicio consulta el consentimiento autoritativo antes de cada fuente y antes de entregar
el perfil. Persiste una instantanea inmutable y su auditoria en la misma transaccion. Cada
cliente tiene versiones crecientes; incluye modelo, consentimiento/version, fechas, fuentes,
indicadores minimos e identificador de la cotizacion. No guarda movimientos bancarios ni documentos.
El perfil vence a los quince minutos o antes si vence una fuente o la autorizacion.

Las lecturas comprueban titularidad, integridad del contenido, vigencia y la version exacta del
consentimiento. Revocar bloquea nuevas lecturas y usos; volver a autorizar no rehabilita perfiles
antiguos. Los rechazos quedan auditados y el `X-Correlation-ID` cruza los servicios.

SOL-326 exige aprobacion explicita vigente, actor igual al titular, finalidad `cotizacion`,
terminos `1.0`, el dato `indicador_riesgo` y ambas fuentes cubiertas por el alcance.
Rechazos, permisos parciales y evidencia legada incompleta deniegan **antes de consultar
cualquier fuente**. No se infieren permisos adicionales ni se calcula un perfil parcial.
`FuenteMock` es el adaptador integrado detras del puerto: simula la respuesta Open Finance/
Open Data dentro del proceso. No hay que desplegar otra API ni conectar proveedores reales.

El consumidor persiste evento, acuse y auditoria atomicamente en `eventos_consentimiento`
y `consultas_perfilamiento`. `eventoId` es idempotente y cliente/version es unico;
contenido distinto con el mismo identificador o version produce 409. La mayor version
durable se usa en cada PEP y no retrocede por entregas desordenadas. El acuse contiene
`eventoId`, `versionAplicada` (mayor version observada) y `aplicadaEn`; un duplicado
devuelve su acuse original. Un evento nunca concede permisos por si mismo: siempre se
requiere la consulta autoritativa vigente a Identidad. El evento tambien bloquea
respuestas autoritativas antiguas y la reutilizacion de perfiles ya persistidos.

Antes de desplegar ejecutar `init-db` con la imagen nueva y la **misma** base existente:
crea la tabla nueva sin borrar perfiles/auditoria; no altera columnas existentes.
El Job `deploy/k8s/perfilamiento/migrate-job.yaml` integra ese paso en `deploy.sh`.
Ver el [rollout coordinado](../../deploy/k8s/identidad/README.md#sol-326-consentimiento-y-outbox).

`PERFILAMIENTO_MOCK_ESCENARIO` acepta `disponible` (default), `no_disponible` o `vencido`, solo
como configuracion del servicio. No se permite elegir el escenario desde una peticion publica.
El cliente recibe 403 por consentimiento, 404 por perfil inexistente/ajeno, 422 por perfil vencido,
409 por version concurrente y 503 por dependencia o integridad fallida. No se entregan perfiles
parciales, ni un fallback que parezca exitoso.

El adaptador de Identidad limita cada llamada a 700 ms. En la generación de perfiles no existe
cache Redis, batch, proyección analítica, circuit breaker ni fallback automático: no se inventan
consumidores ni se acredita propagación hacia ellos. No se contempla conectar proveedores reales.
El hash local detecta alteraciones del contenido, pero no sustituye WORM ni una firma. El chequeo
previo al uso no es una transacción distribuida con Identidad; con las fuentes locales simuladas
no hay divulgación externa, y estas pruebas no demuestran la propiedad de cero divulgaciones para
un adaptador real.

## Consulta autorizada HU44

La consulta de información usa `socioId + operationId` como clave idempotente,
minimiza por intersección entre `datosSolicitados` y los datos consentidos y registra
por fuente la operación, actor, cliente, producto, finalidad, fecha y resultado.
Consulta el consentimiento autoritativo en cada intento, por lo que una revocación
bloquea inmediatamente nuevas consultas.

Cada fuente tiene un circuit breaker que abre tras dos fallos y vuelve a probar a los
30 segundos. La política es fail-closed: una fuente lenta, fallida o con circuito
abierto cancela la consulta completa; no se persiste ni devuelve una respuesta
parcial y no se reutilizan datos históricos como fallback.
