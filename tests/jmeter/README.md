# Apache JMeter: backend integrado

Suite adicional, no reemplazo de pytest ni de `services/cotizacion/loadtest/cotizacion.js`
(k6). Los planes JMX nativos se generan desde `plans.py` para mantener legibles los
asserts, usar fechas futuras y producir una copia exacta del plan junto a cada resultado.
No requiere plugins de JMeter.

## Preparacion y ejecucion

Python 3.12, Java 17 y Git. Desde la raiz del backend, en un entorno virtual:

```powershell
python -m venv .venv-jmeter
.\.venv-jmeter\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
Get-ChildItem services -Directory | Where-Object { Test-Path (Join-Path $_.FullName 'pyproject.toml') } | ForEach-Object { python -m pip install -e $_.FullName }
python -m tests.jmeter.install
python -m tests.jmeter.run --jmeter-home reports\tools\apache-jmeter-5.6.3
```

En Linux, activar el entorno con `source .venv-jmeter/bin/activate`; instalar servicios:

```sh
for project in services/*/pyproject.toml; do
  python -m pip install -e "$(dirname "$project")"
done
python -m tests.jmeter.install
python -m tests.jmeter.run --jmeter-home reports/tools/apache-jmeter-5.6.3
```

El instalador descarga Apache JMeter 5.6.3 del sitio oficial y verifica SHA-512 fijado
en codigo antes de extraerlo. No reemplaza instalaciones existentes. Si ya esta
instalado, pasar su directorio directamente con `--jmeter-home`.
Cada ejecucion necesita un `--output` nuevo (por defecto `reports/jmeter`): no concatena
JTL de ejecuciones distintas ni borra evidencia anterior.

El runner crea bases SQLite temporales, aplica las migraciones reales de Identidad,
Cotizacion y Polizas e inicializa el esquema real de Perfilamiento. Arranca las seis
aplicaciones Flask existentes en loopback y puertos efimeros. Usa JWT RSA/JWKS real,
tokens internos aleatorios y el hashing de contrasenas normal de Identidad. No reemplaza
gateways, no usa Flask test_client ni mocks de servicios propios. Las llamadas entre
BFF, Identidad, Perfilamiento, Cotizacion y Polizas viajan por HTTP.

`DATABASE_URL`, `DB_SCHEMA` y `POLIZAS_DATABASE_URL` deben estar sin definir: el
bootstrap se niega a arrancar si pudieran redirigir una migracion fuera del entorno.
No consume credenciales AWS ni acepta un destino remoto. Los datos y claves se eliminan
al cerrar normalmente. No usarlo como servidor de produccion.

## Cobertura ejecutable

| Area actual | Escenarios JMeter |
| --- | --- |
| Identidad y BFF web | Registro, duplicado, login correcto/incorrecto, JWT ausente/forjado, sesion actual, refresh, logout y refresh revocado |
| BFF movil | Registro y login por `/movil/v1`, sesion, refresh, logout y refresh revocado; rechazo cruzado de JWT web/movil |
| Catalogo | Los ocho detalles de seguros, listado, destinos de viaje y producto inexistente |
| Consentimiento | Ausente, autorizacion, version obsoleta, revocacion inmediata de consultas, nueva autorizacion que no revive el perfil anterior |
| Cotizacion/perfilamiento | Datos invalidos, clave ausente, perfil y fuentes declaradas simuladas, tres ofertas, GET de solicitud, replay con mismo ID y conflicto de payload |
| Productos | Automotive, life, device-protection, payment-protection, mortgage-life, travel, parametric y parametric-weather; cada uno recorre oferta, seleccion, suscripcion, pago simulado y poliza academica |
| Ownership | Otro JWT real no accede a solicitud/ofertas/pago, ni paga una solicitud ajena; dashboard vacio y rechazo de clienteId por query |
| Persistencia e idempotencia | Una cotizacion, saga, pago y poliza por operacion; importe de pago igual a seleccion; reintentos conservan referencia; consulta dashboard |
| Recuperacion real | Reiniciar aplicacion Polizas con la misma DB; detener su servidor antes de un pago nuevo, exigir503, conservar pago sin poliza, reiniciar y reintentar dos veces para emitir exactamente una |

El smoke ejecuta ocho contrataciones y una novena durante la falla controlada.
La fase de reinicio prueba persistencia del servicio Polizas, no un reinicio completo
de los seis procesos. La recuperacion es por reintento HTTP del cliente, no por un
worker de conciliacion inexistente. La respuesta de pago conserva la instantanea
`saga=pago_confirmado`; la consulta de suscripcion y el dashboard verifican `emitida`.

Los tests pytest existentes siguen cubriendo, entre otros, vencimiento del perfil,
fuente parcial no disponible, acuse de emision perdido, migraciones, concurrencia de
idempotencia, reglas de negocio, contratos, errores internos y limitacion de intentos.
Ejecutarlos con los comandos de [CI](../../docs/ci.md); esta suite no afirma reemplazar
toda esa matriz ni que un escenario HTTP equivalga a cobertura de lineas.

## Rendimiento local acotado

Opt-in, secuencial por defecto y nunca contra AWS:

```powershell
python -m tests.jmeter.run --jmeter-home reports\tools\apache-jmeter-5.6.3 --mode performance --iterations 20 --allow-load --output reports\jmeter-performance
```

Cada iteracion registra un usuario nuevo y crea ocho operaciones distintas; los reintentos
no cuentan como throughput. `--threads` permite concurrencia independiente. El limite
del harness es 200 iteraciones totales (threads por iterations), no una prueba de escala
masiva. Los identificadores y cuentas se generan por ejecucion/thread/iteracion.

Los Transaction Controllers incluyen el tiempo de pared de todo el recorrido indicado,
incluyendo el procesamiento del generador entre HTTP; no miden solo Cotizacion interna.
Login y registro quedan fuera de las transacciones de negocio. No hay warmup descartado:
la primera operacion y su compilacion Groovy cuentan. Se exige cero assertions fallidos,
conteo exacto de recorridos y al menos 20 muestras por grupo para activar percentiles
nearest-rank. Veinte muestras es un chequeo inicial, no significancia estadistica de p99.
El smoke verifica funcionalidad sin convertir una sola muestra en evidencia de SLO.

| Grupo | Recorrido medido | Gate p95 / p99 |
| --- | --- | --- |
| `journey.quote.*` | BFF: crear solicitud (incluye perfil y calculo) + consultar ofertas | 250 / 500 ms |
| `journey.quote.mortgage-life` | Mismo flujo, vida hipotecaria con perfil y precio | 400 / 800 ms |
| `journey.emission.*` | Aceptar oferta + suscripcion + pago/emision + GET de saga emitida | 1500 / 3000 ms |
| `journey.dashboard` | Consulta BFF que agrega cotizaciones, pagos, suscripciones y polizas | 150 / 300 ms |

Los gates toman presupuestos del Enunciado p11; dashboard es una consulta agregada real
con polizas, **no** evidencia de un endpoint de siniestros inexistente. La cotizacion via
BFF tampoco certifica el ingreso embebido de socios aun no implementado. Un fallo de
presupuesto produce salida1 y conserva medidas: nunca se eleva el umbral para dejar verde.

`summary.json` separa recorridos ofrecidos, completados y fallidos, creaciones aceptadas
HTTP201, rechazadas HTTP4xx y resultados tecnicos/desconocidos; ademas cuenta filas durables
unicas. Las respuestas negativas deliberadas del smoke son asserts esperados, no carga
de negocio aceptada. No hay modelo open-loop ni garantia de RPM ofrecidas: el generador
es cerrado y no certifica ASR-ESC.

## Evidencia y CI

`run.py` valida el JTL aunque JMeter devuelva codigo0: un assert fallido, muestras ausentes,
truncamiento de recorridos, duplicados persistidos o un umbral excedido fallan el comando.
`tests/test_jmeter.py` comprueba XML, evaluacion fail-closed, limites y percentiles.

Se guardan JMX, JTL CSV y resumen JSON con revision y configuracion. No se guardan cuerpos,
cabeceras, URLs, tokens, claves privadas ni mensajes de assertion con valores interpolados.
Los logs de motor quedan locales para diagnostico y **no** se publican como artefactos
(pueden contener rutas y nombres del equipo). No habilitar View Results Tree ni debug HTTP
en evidencia compartida. Las claves y credenciales solo existen en memoria; los passwords
de las cuentas sinteticas no pertenecen a usuarios reales.

`.github/workflows/jmeter.yml` se ejecuta solo mediante `workflow_dispatch`: por ahora
no corre JMeter en PR ni push. Por defecto valida la muestra pequena del smoke
(nueve operaciones de negocio incluyendo recuperacion). El rendimiento requiere
activar expresamente `performance`, desactivado por defecto. Conserva evidencia aun
al fallar, sin instalar ni ejecutar JMeter dentro del job pytest existente.
Pytest mantiene las pruebas del generador y evaluador, sin lanzar el motor JMeter.
Jenkins y k6 conservan su comportamiento.

## Backend compartido con Cypress y Espresso

```powershell
python -m tests.jmeter.local_stack --web-port 5000 --mobile-port 5001
```

Web: `http://127.0.0.1:5000`, rutas `/v1/...`, readiness `/ping`.
Movil: `http://127.0.0.1:5001`, rutas `/movil/v1/...`, readiness `/ping`.
Android Emulator puede usar `adb reverse tcp:5001 tcp:5001` y base local
`http://127.0.0.1:5001/movil/v1`. El proxy web debe apuntar al puerto5000.
No se modifica CORS/autenticacion del producto para que pasen pruebas.

No hay usuarios sembrados. Registrar `POST /v1/usuarios` (movil:
`/movil/v1/usuarios`) con `nombre`, `correo` unico `@example.com` y `contrasena`
`Solventa123!`; login en `/v1/sesiones` o `/movil/v1/sesiones`. Antes de cotizar,
web debe autorizar `PUT /v1/consentimientos/perfilamiento` con Bearer y
`{"autorizado":true,"version":0}`. Perfilamiento y Polizas estan habilitados.
La vigencia de la DB dura todo el proceso, por lo que recargas y relogins consultan
persistencia real. SIGINT/SIGTERM cierra servidores y elimina datos temporales.
Para CI de otros repos, fijar el SHA que contiene este bootstrap, no un develop anterior.

## Limites frente a los documentos rectores

Prevalecen Arquitectura v7 y Enunciado sobre estrategias de pruebas previas.
Esta ejecucion usa SQLite/Werkzeug local, **no PostgreSQL ni un despliegue escalado**.
Open Finance/Open Data son las fuentes mock del producto; pago y poliza son academicos.
No hay firma/cobro real, canal socios completo, estado de siniestros ni notificaciones.

No se certifican: presupuesto externo120ms/timeout700ms ni fallback/circuit breaker;
ASR-FIN/EMI convergencia automatica60s, conciliacion externa/ledger/firma; PRV propagacion
a caches/batch/analitica en5min; AUD WORM y reconstruccion en5min. El smoke solo demuestra
las partes implementadas: revocacion autoritativa y reanudacion idempotente academica.

Arquitectura p33 exige baseline500rpm p95<200/p99<400 con0%errores, pico ofrecido>=50506/min,
completado>=50000/min, errores<1%, p95<=250/p99<=500 y capacidad adicional<=60s.
Nada de eso se infiere de este harness cerrado local. Faltan ingreso/mezcla de socios,
entorno PostgreSQL representativo, generador open-loop calibrado y telemetria de escalado.
Esas brechas requieren experimentos autorizados aparte, no skips ni una plantilla que
aparente cumplirlos.

### Primera medicion local (2026-10-21 UTC)

Windows/Java17/SQLite, un thread y20 iteraciones:160 operaciones aceptadas/completadas,
160 pagos y160 polizas unicas, sin errores funcionales. El gate de rendimiento devolvio1:
siete grupos de cotizacion excedieron250/500ms. Por ejemplo automotive459/1078ms y
life464/734ms (p95/p99); mortgage-life273/589ms y dashboard72/99ms.
No es una medicion de PostgreSQL ni de infraestructura escalada. Se conserva como
resultado negativo, sin atribuir su causa exclusivamente a la aplicacion o al generador,
sin elevar presupuestos y sin declarar cumplimiento arquitectonico.
