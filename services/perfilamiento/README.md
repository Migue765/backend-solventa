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
| POST | `/v1/eventos/consentimientos` | Evento interno del Outbox de Identidad |

El servicio consulta el consentimiento autoritativo antes de cada fuente, reintento, fallback
y antes de entregar
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
409 por referencia concurrente y 503 por dependencia o integridad fallida. No se entregan perfiles
parciales ni se inventa un valor por defecto de riesgo.

## SOL-201: consultas resilientes integradas

`POST /v1/perfiles` admite ademas `fuentes`, `finalidad` y `datos`. Por compatibilidad,
omitirlos selecciona los adaptadores registrados, `cotizacion` e `indicador_riesgo`.
La referencia `consentimientoId` + `consentimientoVersion` se conserva y valida. Una
finalidad/dato no soportado, fuente desconocida, o permiso incompleto no dispara consultas.
Cada adaptador implementa `FuentePerfilamiento` y entrega `Fuente` normalizada; el nombre
es extensible, sin cambiar consumidores. Un perfil de riesgo sigue requiriendo al menos
Open Finance y Open Data; una fuente nueva exige estar expresamente cubierta por el
consentimiento autoritativo (el catalogo actual de Identidad solo autoriza las dos iniciales).

`PoliticaConsultas` fija objetivo **120 ms** por dependencia y techo **700 ms** por llamada.
Un deadline monotono compartido limita el trabajo de dependencias a **400 ms** dentro de
un presupuesto local **450 ms**, reservando **50 ms** para persistencia/respuesta. La fuente
reserva ademas 120 ms del tiempo restante para revalidar autorizacion y resolver fallback.
Por eso puede cortarse antes de 700 ms. Los tiempos de BD y planificacion del sistema
operativo no son cancelables por este deadline; la reserva no constituye garantia de
p95/p99 del journey. Medir tambien HTTP extremo a extremo en el despliegue.

Hay como maximo **dos intentos** por consulta de fuente, solo ante falla transitoria y
si queda presupuesto; timeout, saturacion o respuesta invalida no se reintentan. Cada
intento revalida permiso. El circuit breaker por fuente/proceso abre tras **tres consultas
fallidas**, espera **30 s** y admite una unica sonda de recuperacion. Reiniciar el proceso
reinicia el breaker, no la evidencia persistida. El ejecutor admite ocho operaciones
simultaneas sin cola: una operacion tardia conserva su cupo hasta terminar y su resultado
no se publica ni persiste. Python no interrumpe threads; cualquier futuro adaptador de
red debe imponer tambien timeout nativo/cancelacion de transporte. No se afirma que un
thread lento sea una conexion bancaria cancelada.

El fallback solo usa `cache_fuentes`, ligada a cliente, consentimiento/id/version y
nombre de fuente; valida integridad, fecha original, maximo 15 minutos y permiso vigente.
No renueva timestamps, no reutiliza otra version y no autoriza consentimiento legado.
Revocacion/rechazo/vencimiento bloquean incluso cache y perfiles ya creados.
La cache se persiste en la misma transaccion que perfil/auditoria. Si una fuente falla
sin cache util, la respuesta 503 incluye estados por fuente sin indicadores ni detalle
del proveedor; las fuentes exitosas se conservan con la auditoria, tras revalidar permiso.
No se crea cotizacion con riesgo parcial. Una consulta posterior puede reutilizar esa
evidencia cuando otra fuente falle, bajo las mismas condiciones.

Los perfiles completos incluyen `estadoFuentes` con `{fuente, estado, intentos, motivo}`;
`estado` es `disponible` o `cache`, y `fallbackUsed` indica si se utilizo cache.
El error parcial usa `fuentes` con esos estados y `fallida` para la afectada. Cotizacion
acepta la extension y preserva la evidencia hasta el BFF. Perfiles historicos sin estos
metadatos siguen siendo legibles si `fallbackUsed` es falso; no se admite inconsistencia
entre metadatos, fuentes y fallback. Los adaptadores integrados **siguen siendo sinteticos**.

`consultas_perfilamiento.contenido` registra estados, intentos y
`dependencias[{dependencia,duracionMs,objetivoExcedido,resultado}]`, mas `duracionMs`
(antes del commit), `presupuestoMs` y correlacion. No registra mensajes de error del
proveedor, movimientos ni credenciales. Las denegaciones iniciales tienen fuentes vacias.
`versiones_perfilamiento` asigna version atomica por cliente en la transaccion del perfil;
inicializa el contador desde perfiles historicos y evita colisiones entre cotizaciones
simultaneas. `init-db` crea ambas tablas aditivamente; no borra datos.

Publicar primero Cotizacion compatible, ejecutar el Job de migracion con la imagen
nueva y publicar Perfilamiento. Ver [orden SOL-201](../../deploy/k8s/perfilamiento/README.md#sol-201).
El guard del frontend debe aceptar evidencia cache vigente y los metadatos completos,
sin reducir verificaciones de consentimiento, fechas o integridad. No publicar el recorrido
completo sin esa compatibilidad y las dependencias SOL-203/SOL-326.

No existe Redis, batch ni proyeccion analitica en este recorrido: no se inventan consumidores
ni se acredita propagacion hacia ellos. El hash local detecta alteraciones del contenido, pero no
sustituye WORM ni una firma. El chequeo previo al uso no es una transaccion distribuida con
Identidad; con las fuentes locales simuladas no hay divulgacion externa, y estas pruebas no
demuestran la propiedad de cero divulgaciones para un adaptador real.
