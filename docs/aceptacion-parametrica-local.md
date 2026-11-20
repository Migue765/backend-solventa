# Aceptacion local del disparo parametrico automatico

Esta prueba acredita un recorrido **local y academico**: un evento externo
**sintetico** recibido por HTTP produce un siniestro nuevo, evalua una regla y
termina en un pago **simulado**, sin aprobar ni pagar manualmente el siniestro.
No basta con consultar un caso previamente pagado.

## Ejecucion

Con Python 3.12 y las dependencias de desarrollo y de Identidad, Polizas,
Siniestros y ambos BFF instaladas en un entorno exclusivo del checkout:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\test_parametric_acceptance_flow.py
```

El test usa `tests/claims_local_stack.py` sin modificarlo: levanta servicios HTTP
reales en loopback con puertos dinamicos, migraciones y SQLite temporal. Registra
dos clientes y sus sesiones JWT. El harness ya crea un aviso **asistido** por
cliente; la prueba toma ese inventario inicial y exige un ID parametrico nuevo.
Las consultas SQL del driver son solo de lectura, nunca crean casos ni pagos.

## Recorrido y evidencia exigida

1. Preparacion: emite una poliza `parametric-weather` simulada para el cliente
   mediante la API interna de Polizas. Sus identificadores sinteticos de
   suscripcion/pago son evidencia academica de emision, **no** una indemnizacion
   precargada. Registra una regla con fuente, ventana, ubicacion, precipitacion,
   umbral, beneficiario y monto sinteticos.
2. Fuente externa simulada: envia `POST /internal/v1/eventos-parametricos/recepcion`
   por HTTP con `X-Fuente-ID` y Bearer de **fuente**, no el token interno.
   Exige `202`, un aceptado, cero duplicados y un pendiente. Todavia no debe
   existir un siniestro nuevo, decision, caso ni pago.
3. Evaluacion: el driver lanza el entrypoint existente
   `python -m siniestros.parametric_worker --once --limit 1` como subproceso.
   Consulta `GET /v1/siniestros` del BFF web con JWT del cliente y exige
   exactamente un ID nuevo. El alias
   `GET /v1/clientes/me/siniestros/{id}` debe mostrar tipo `parametrico`,
   estado `aprobado` e historial `reportado -> aprobado`.
4. Indemnizacion academica: lanza
   `python -m siniestros.payment_worker --once --limit 1`, sin cambiar el worker.
   El mismo alias debe mostrar `pagado` e historial
   `reportado -> aprobado -> pagado`. La consulta interna de trazabilidad y las
   lecturas SQL enlazan evento, regla/version, decision valida, caso, siniestro,
   beneficiario, importe, pago confirmado y su unico efecto durable simulado.
5. Deduplicacion: reenvia exactamente el mismo evento, cuerpo y fuente; exige
   `202`, cero aceptados, un duplicado y cero pendientes. Relanza ambos
   entrypoints y exige el mismo siniestro, caso, pago, historial y trazabilidad,
   con un solo evento, decision, caso, pago, intento y efecto durable.
6. Aislamiento: el segundo cliente conserva su inventario original y recibe
   `404 siniestro_no_encontrado` al consultar el alias del nuevo siniestro.

El driver controla el arranque acotado de los workers: cada subproceso tiene
timeout de 30 segundos y `--once`, sin esperas arbitrarias. No llama endpoints
de procesamiento, aprobacion, conciliacion ni pago como sustituto del worker.
Las credenciales efimeras quedan en memoria del driver y entorno exclusivo de
cada subproceso; no se pasan por argumentos, navegador ni archivos de estado.
Los errores de subproceso son visibles con credenciales redactadas. El contexto
del harness cierra servidores, hilos y conexiones; pytest gestiona los archivos
temporales. No se toca la base de desarrollo ni servicios externos.

## Limites de la evidencia

- **SQLite/test local**, no PostgreSQL, concurrencia distribuida, rendimiento,
  SLA ni despliegue autonomo verificados. Los workers se invocan secuencialmente
  en modo acotado; no se prueba su supervision o programacion en produccion.
- El evento no procede de un proveedor real; OpenFinance/OpenData siguen
  mockeados. La indemnizacion usa el simulador existente, no una transferencia
  bancaria, AWS ni una integracion real de pagos.
- No verifica la UI, JWE, notificaciones, todos los operadores de reglas,
  recuperacion de caidas ni criterios de Jira no confirmados. Los tests de
  integracion de Siniestros siguen cubriendo reglas y pagos con mayor detalle.
- El PR agrega evidencia de aceptacion y este documento, no motor, workers,
  endpoints de negocio ni una UI de pagar.
