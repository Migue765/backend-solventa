# Notificaciones

Servicio dueño de las preferencias, la programación y la trazabilidad de entrega de
notificaciones de clientes. Implementa HU22-BE y es la contraparte de SOL-174 móvil.

## Eventos y minimización

`POST /v1/eventos` recibe eventos internos autenticados. El contrato completo está en
`contracts/openapi/notificaciones-internal-v1.yaml` y solo admite:

- pago paramétrico ejecutado;
- pago confirmado;
- cambio visible y relevante del siniestro;
- póliza emitida con su fecha final.

El productor envía `eventoId`, cliente, fecha, recurso de destino y únicamente el dato
necesario para la regla. Campos adicionales son rechazados. El contenido se construye
desde plantillas del servicio: no copia importes, cuentas, documentos, diagnósticos ni
texto interno del evento.

`eventoId` es la clave idempotente. Una repetición idéntica responde `200` y no crea ni
entrega otra notificación; el mismo identificador con datos diferentes responde `409`.
El evento origen se conserva incluso cuando las preferencias deshabilitan todos los
canales.

## Preferencias, programación y entrega

Los tipos configurables son `pagos`, `siniestros`, `vencimientos` y `renovaciones`.
Los canales son `in_app`, `email` y `push`. Sin configuración explícita, solo `in_app`
está habilitado. El BFF móvil expone lectura y actualización sin aceptar un id de cliente
en URL o cuerpo.

Una póliza emitida programa:

- vencimiento: `EXPIRATION_NOTICE_DAYS` antes de `fechaFin` (7 por defecto);
- renovación: `RENEWAL_NOTICE_DAYS` antes de `fechaFin` (30 por defecto).

`POST /v1/despachos` procesa hasta 100 elementos vencidos y debe invocarse
periódicamente por el programador del entorno. Cada intento queda registrado. Los
fallos se reintentan con espera exponencial hasta `NOTIFICATION_MAX_ATTEMPTS` (5 por
defecto); luego quedan en estado `fallida` con el último error.

Cada reserva de entrega vence a los cinco minutos. Una reserva abandonada se registra
como `reserva_expirada` y vuelve a pendiente mientras conserve intentos. Los adaptadores
externos deben enviar `notificacion_id` como clave idempotente al proveedor; así una
respuesta perdida y su reintento no generan dos mensajes.

El canal `in_app` se confirma al persistirlo y aparece en
`GET /v1/notificaciones`. Los canales externos no simulan éxito: mientras no exista un
adaptador de proveedor quedan pendientes o fallidos como `proveedor_no_configurado`.

## Persistencia

Las tablas son:

- `eventos_notificacion`: evento origen, cliente, fecha, huella y resultado;
- `preferencias_notificacion`: decisión por cliente, tipo y canal;
- `notificaciones`: recurso de destino, programación, estado e intentos;
- `intentos_entrega_notificacion`: fecha, resultado y error de cada entrega.

La creación del evento y sus notificaciones ocurre en una transacción. La migración
inicial es `0001_notificaciones`.

## Configuración

| Variable | Requerida | Descripción |
|---|---:|---|
| `NOTIFICACIONES_DATABASE_URL` | Sí | URL SQLAlchemy de la base propia |
| `INTERNAL_API_TOKEN` | Sí | Credencial interna compartida con BFF y productores |
| `EXPIRATION_NOTICE_DAYS` | No | Anticipación de vencimiento; default 7 |
| `RENEWAL_NOTICE_DAYS` | No | Anticipación de renovación; default 30 |
| `NOTIFICATION_MAX_ATTEMPTS` | No | Máximo de intentos; default 5 |

## Ejecución y validación

Desde la raíz del repositorio:

```powershell
python -m pip install -e ".\services\notificaciones[dev,postgres,server]"
$env:NOTIFICACIONES_DATABASE_URL = "sqlite:///notificaciones-local.db"
$env:INTERNAL_API_TOKEN = "<credencial local>"
python -m alembic -c services\notificaciones\alembic.ini upgrade head
python -m flask --app notificaciones.app run --port 5006
python -m pytest services\notificaciones\tests
python -m mypy --config-file services\notificaciones\mypy.ini services\notificaciones\src services\notificaciones\tests
```
