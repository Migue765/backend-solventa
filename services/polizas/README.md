# Polizas academicas

Servicio interno con base de datos propia. Una suscripcion aprobada y un **pago
simulado nuevo confirmado** generan una poliza academica persistida. No hay aseguradora,
firma, cobro ni cobertura real. Cada respuesta incluye `simulada: true`.
El BFF conserva la autenticacion JWT y deriva el cliente del principal; nunca
acepta un cliente indicado por el navegador para consultar el tablero.

## Regla autorizada para la simulacion

- Auto, vida, dispositivos, proteccion de pagos y vida hipotecaria: inicio en
  el dia UTC de confirmacion del pago y fin un mes calendario despues, ajustando
  al ultimo dia del mes cuando corresponde (31 de enero -> 28/29 de febrero).
  Esta es una regla academica explicita, no una deduccion de la periodicidad
  de prima ni la duracion comercial del catalogo.
- Viaje: `departureDate` y `returnDate` contratadas.
- Vuelo: `flightDate` como inicio y fin.
- Clima: `startDate` y `endDate` contratadas.

Ambas fechas son inclusivas y se entregan como `YYYY-MM-DD`. Estado respecto
del dia UTC: `programada` antes del inicio, `vigente` dentro del intervalo,
`vencida` despues del fin. No se simulan renovaciones automaticas.

## API interna

Ambas rutas exigen el `INTERNAL_API_TOKEN` compartido con Cotizacion como Bearer.
No se publican directamente en API Gateway ni en el ALB.

`POST /v1/polizas` recibe evidencia exclusivamente del servicio Cotizacion:
`{clienteId, suscripcionId, pagoId, tipo, confirmadaEn, simulada:true,
fechasContratadas:null|{inicio,fin}}`. Los tres IDs son UUID; `confirmadaEn`
es un instante ISO con zona. Los productos fechados requieren fechas
contratadas; los otros cinco las rechazan. La evidencia no incluye documentos,
correo, contrasenas, datos de pago ni el formulario de producto completo.

Devuelve `200` con `{id,suscripcionId,tipo,fechaInicio,fechaFin,estado,simulada:true}`.
Una unica poliza por suscripcion y pago, protegida por restricciones de base;
repetir la misma evidencia devuelve el mismo ID. Reutilizar pago/suscripcion
con evidencia diferente responde `409`. Evidencia invalida: `400`;
credencial ausente/incorrecta: `401`; fallo de persistencia: `503`.
Se guardan hash de evidencia, version de regla y fecha de emision para auditoria.

`GET /v1/polizas?clienteId=<UUID>` devuelve `{polizas:[...]}` solo de ese
cliente, ordenadas por emision descendente. No admite cuerpo ni otros
parametros. Todas las respuestas incluyen `Cache-Control: no-store` y
`X-Correlation-ID`. `/health` solo comprueba el proceso; no demuestra integracion.

## Renovacion academica (HU33, SOL-317)

Componente Ciclo de Vida de Polizas (`renovar()` del modelo de dominio). Aplica
ASR-EMI-01 (la vigencia solo cambia con aceptacion y pago confirmados),
ASR-FIN-01 (un solo cobro por renovacion) y ASR-AUD-01 (instantanea de condiciones).
Las rutas exigen el token interno y `X-Cliente-ID`; el BFF Web lo deriva del JWT.

**Reglas `academica-renovacion-v1`:**
- Renovables: `automotive`, `life`, `device-protection`, `payment-protection` y
  `mortgage-life` (vigencia mensual). Viaje, vuelo y clima se contratan por fechas:
  `producto_no_renovable`.
- Ventana: desde 15 dias antes de `fechaFin` hasta `fechaFin`, solo con la poliza
  vigente (`poliza_programada`, `poliza_vencida`, `fuera_de_ventana`).
- Nueva vigencia contigua: inicia el dia siguiente al fin actual y dura un mes
  calendario con la misma regla de la emision. No se crea otra poliza: se extiende
  `fechaFin` de la misma poliza y el periodo queda en `renovaciones`.
- Prima: tarifa academica fija por producto (por ejemplo, vida 25.000 COP). Polizas
  no conserva el perfil de riesgo de la emision, asi que no recalcula la prima con
  Perfilamiento ni Cotizacion. Coberturas y condiciones son las del catalogo.

`GET /v1/polizas/{polizaId}/renovacion` devuelve `renovable`, `motivo`, `ventana`,
`vigenciaActual`, la `oferta` (nueva vigencia, coberturas, prima, condiciones,
`requierePago`, `reglaVersion`, `condicionesHash`) y el `ultimoIntento`.
Una poliza inexistente o de otro cliente responde `404 poliza_no_encontrada`.

`POST /v1/polizas/{polizaId}/renovaciones` con `Idempotency-Key` (UUID) y
`{aceptaCondiciones:true, condicionesHash}`:

1. Valida propietario y elegibilidad (`422 poliza_no_renovable` con `motivo`) y
   que el hash coincida con la oferta vigente (`409 condiciones_cambiaron`).
2. Registra el intento `pago_pendiente` con la instantanea de condiciones **antes**
   del cobro. `reserva_periodo` (unica) admite una sola renovacion activa por periodo:
   otra clave responde `409 renovacion_en_curso` con `renovacionId`.
3. Cobra con el simulador (`PAGO_RENOVACION_SIMULADO=confirmado|rechazado`). El efecto
   se guarda en `pagos_renovacion` con la renovacion como clave primaria: un reintento
   consulta el mismo efecto y nunca cobra dos veces.
4. Con pago confirmado extiende `fechaFin` con un `UPDATE` condicionado al fin anterior
   y marca `renovada`. Con pago rechazado marca `rechazada` (`pago_rechazado`), libera
   la reserva y la poliza queda igual. Un resultado ambiguo deja `pendiente` (202).

Respuestas: `201` intento nuevo con resultado definitivo, `200` misma clave y misma
solicitud, `202` pendiente, `409 idempotencia_conflictiva` si la clave se reutiliza con
otra solicitud. Un pendiente se recupera repitiendo la misma clave o con
`flask --app polizas.app conciliar-renovaciones`, que resuelve los pendientes con mas
de 30 s; no hay worker programado.

## Integracion y recuperacion

Cotizacion activa emision con `POLIZAS_SERVICE_URL` e `INTERNAL_API_TOKEN`.
La migracion `emision_polizas` agrega una marca inicialmente falsa a los pagos
historicos. Solo los pagos creados con la integracion habilitada tienen
`emision_solicitada=true`; una lectura o reintento de pagos anteriores no emite.
No hay backfill ni consumidor automatico de eventos historicos.

Un intento rechazado por el simulador de Cotizacion no invoca este servicio,
no confirma un pago ni genera poliza. El rechazo es un resultado de negocio
persistido separado de un fallo de emision o acuse perdido.

El pago y su outbox se confirman primero en Cotizacion. A continuacion se
solicita la emision idempotente y solo tras recibir un ID se avanza la saga
a `emitida`. El pago conserva su instantanea `pago_confirmado` version 2;
GET suscripcion muestra la saga actual version 3. Si falla la emision o su
confirmacion, POST pago devuelve `503`: **no debe interpretarse como pago
no persistido**. Repetir POST pago de la misma solicitud recupera el mismo
pago y completa la emision sin duplicarla, incluso si vencio la cotizacion.
No se promete entrega automatica sin ese reintento; no hay worker implementado.

Si se pierde el acuse despues de persistir la poliza, el dashboard puede listar
esa poliza mientras GET suscripcion aun muestra `pago_confirmado` version 2.
Las lecturas no completan la saga: repetir POST pago con la misma solicitud y
clave recupera la operacion y permite avanzar a `emitida` version 3, sin duplicar
pago, poliza ni eventos. La respuesta del pago sigue conservando su instantanea
version 2; una poliza visible no sustituye la confirmacion de la saga.

GET dashboard consulta las dos persistencias via HTTP, sin leer tablas ajenas,
retorna `polizas` reales y falla con `503` si Polizas no responde. No emite ni
modifica el negocio en una lectura. Sin `POLIZAS_SERVICE_URL` se conserva el
marcador legado `no_implementado`, nunca una lista vacia artificial.
Suscripciones y pagos siguen presentes para los otros consumidores.

## Ejecucion y validacion

Desde la raiz, en PowerShell:

```powershell
python -m pip install -e ".\services\polizas[dev]"
$env:POLIZAS_DATABASE_URL = "sqlite:///polizas-local.db"
$env:INTERNAL_API_TOKEN = "<credencial local, no produccion>"
python -m alembic -c services\polizas\alembic.ini upgrade head
python -m flask --app polizas.app run --port 5004
```

En Cotizacion usar `POLIZAS_SERVICE_URL=http://127.0.0.1:5004` y la misma
credencial. Cada servicio mantiene su base propia. SQLite solo se usa para
desarrollo y pruebas; el despliegue principal requiere PostgreSQL persistente.

`python -m pytest services\polizas\tests tests\test_policies_flow.py` verifica
vigencias de los ocho productos, limites de calendario, JWT del BFF, persistencia,
aislamiento, reintentos, fallos y exclusion de pagos historicos. Esto no sustituye
la aceptacion autenticada desde el frontend desplegado.

Despliegue principal: [runbook](../../deploy/k8s/polizas/README.md).
