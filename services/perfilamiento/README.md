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
| GET | `/v1/perfiles/{perfilId}` | `clienteId` en query |

El servicio consulta el consentimiento autoritativo antes de cada fuente y antes de entregar
el perfil. Persiste una instantanea inmutable y su auditoria en la misma transaccion. Cada
cliente tiene versiones crecientes; incluye modelo, consentimiento/version, fechas, fuentes,
indicadores minimos e identificador de la cotizacion. No guarda movimientos bancarios ni documentos.
El perfil vence a los quince minutos o antes si vence una fuente o la autorizacion.

Las lecturas comprueban titularidad, integridad del contenido, vigencia y la version exacta del
consentimiento. Revocar bloquea nuevas lecturas y usos; volver a autorizar no rehabilita perfiles
antiguos. Los rechazos quedan auditados y el `X-Correlation-ID` cruza los servicios.

`PERFILAMIENTO_MOCK_ESCENARIO` acepta `disponible` (default), `no_disponible` o `vencido`, solo
como configuracion del servicio. No se permite elegir el escenario desde una peticion publica.
El cliente recibe 403 por consentimiento, 404 por perfil inexistente/ajeno, 422 por perfil vencido,
409 por version concurrente y 503 por dependencia o integridad fallida. No se entregan perfiles
parciales, ni un fallback que parezca exitoso.

El adaptador de Identidad limita cada llamada a 700 ms. No se implementan todavia proveedores
reales, cache Redis, circuit breaker, batch, consumidor de revocaciones ni almacenamiento WORM.
El hash local detecta alteraciones del contenido, pero no sustituye WORM ni una firma.
