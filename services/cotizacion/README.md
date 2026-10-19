# Cotización

Microservicio de Tarificación y Cotización de Solventa (Flask).

## Estructura

```
src/cotizacion/
├── app.py                  # create_app: arma dependencias y registra blueprints
├── config.py               # Configuración por variables de entorno
├── api/                    # Blueprints HTTP y esquemas de entrada/salida (pydantic)
├── application/            # Casos de uso y puertos
├── domain/                 # Agregado Cotización, Cobertura Cotizada y Dinero
└── infrastructure/
    ├── persistence/        # Modelos SQLAlchemy, sesión y repositorio
    ├── perfil_riesgo_simulado.py
    └── tarificador_tasa_fija.py
migrations/                 # Migraciones Alembic
tests/
├── unit/                   # Un archivo por módulo, sin base de datos
└── integration/            # API, repositorio y migraciones sobre SQLite
```

## Endpoints

Con `COTIZACION_PERFILAMIENTO_HABILITADO=true`, `PERFILAMIENTO_SERVICE_URL` e
`INTERNAL_API_TOKEN`, cada solicitud genera un perfil remoto autorizado, usa ese
mismo perfil en el calculo y conserva su evidencia en la instantanea de decision.
`GET /v1/solicitudes-cotizacion/{id}/perfilamiento?clienteId={uuid}` comprueba
titularidad, referencia, integridad y vigencia. Ofertas, seleccion y nueva
suscripcion revalidan el consentimiento y el perfil; no hay fallback si fallan.
Las solicitudes antiguas sin evidencia necesitan una nueva cotizacion.
Si la bandera esta deshabilitada se mantiene el flujo previo con un warning.

| Método | Ruta               | Descripción                    |
|--------|--------------------|--------------------------------|
| GET    | `/ping`            | Health check                   |
| POST   | `/v1/cotizaciones` | Crea y calcula una cotización  |
| GET    | `/v1/productos-seguro` | Lista el catálogo de seguros |
| GET    | `/v1/productos-seguro/{productoId}` | Consulta el detalle de un seguro |
| GET    | `/v1/destinos-viaje` | Lista países de destino admitidos |
| POST   | `/v1/solicitudes-cotizacion` | Registra una solicitud idempotente |
| GET    | `/v1/solicitudes-cotizacion/{id}` | Consulta una solicitud del cliente |
| GET    | `/v1/solicitudes-cotizacion/{id}/ofertas` | Compara ofertas simuladas |
| GET/PUT | `/v1/solicitudes-cotizacion/{id}/seleccion` | Consulta o guarda una selección idempotente |
| GET/POST | `/v1/solicitudes-cotizacion/{id}/suscripcion` | Consulta o aprueba una suscripción |
| GET/POST | `/v1/solicitudes-cotizacion/{id}/pago-simulado` | Recupera o confirma un pago exclusivamente simulado |

### Persistencia de suscripción

La suscripción se inserta antes de su decisión y saga mediante `flush`, sin confirmar
la transacción. Suscripción, decisión, saga y outbox `SuscripcionAprobada` se confirman
juntos; cualquier error posterior al `flush` revierte todos esos registros.
Las pruebas de repositorio activan las claves foráneas de SQLite y comprueban tanto
la recuperación persistida como el rollback completo ante una FK inválida en la saga.

### Lectura interna del tablero

`GET /v1/dashboard?clienteId=<uuid>` es una ruta interna para el BFF principal.
Exige `Authorization: Bearer <INTERNAL_API_TOKEN>` con el secreto ya existente
configurado en ambos servicios: sin credencial o con una incorrecta responde `401`, sin
configuracion responde `503` y registra el error. Un JWT de cliente no sustituye
la credencial de servicio. Un UUID ausente/invalido responde `400`.
El canal publico es exclusivamente `GET /v1/clientes/me/dashboard` del BFF,
que obtiene el identificador del JWT y rechaza parametros de suplantacion.

Una consulta SQL de solo lectura filtra cotizaciones por cliente y une seleccion,
suscripcion, saga y pago persistidos con el mismo ownership. Conserva importes,
periodicidades, estados y fechas; no recalcula ofertas ni consulta perfilamiento,
ni siquiera para registros historicos vencidos. Ordena cotizaciones de mas reciente
a mas antigua, con ID como desempate. No suma importes ni convierte monedas.
La consulta sin filas devuelve tres arrays vacios reales. Fallos SQL o integridad
(por ejemplo suscripcion sin saga) se notifican como errores, nunca como vacio.
La respuesta tiene `Cache-Control: no-store`. Con `POLIZAS_SERVICE_URL` habilitado,
se agrega el array de polizas persistidas consultando el servicio por HTTP y
propagando cliente y correlacion; su indisponibilidad responde `503`, no vacio.
El BFF valida el contrato publico. Sin habilitarlo conserva el marcador legado;
notificaciones sigue `no_implementado`.

### Pago simulado

El canal público es el BFF con JWT. Las rutas internas usan `clienteId` derivado
del sujeto autenticado, nunca datos del formulario. POST admite cuerpo ausente o
`{}` y exige `Idempotency-Key` UUID. No recibe importes, tarjetas ni datos bancarios.
Solo una suscripción aprobada, con selección coincidente y cotización vigente,
puede pasar de `pago_pendiente` a `pago_confirmado`. Se conserva importe, moneda y
periodicidad de esa selección (`mensual`, `por_viaje`, `por_vuelo`, `por_periodo`).

La migración aditiva `pagos_simulados` crea una tabla independiente. Pago, avance
de saga (versión +1), fecha de suscripción y outbox `PagoSimuladoConfirmado` se
confirman en una transacción. Unicidad por solicitud/suscripción y por cliente/clave,
más actualización condicional de saga, impiden duplicados en concurrencia.
No se conecta ninguna pasarela ni se realiza cobro, firma o emision real.
La integracion nueva con Polizas emite registros academicos despues del pago
si se configura `POLIZAS_SERVICE_URL` e `INTERNAL_API_TOKEN`. La migracion
`emision_polizas` excluye los pagos anteriores; no hay emision retroactiva.
Un fallo de emision devuelve `503` conservando el pago: repetir POST completa
la emision idempotente y avanza la saga a `emitida` version 3. GET pago conserva
la instantanea de confirmacion version 2, sin efectos de escritura.
Consultar [Polizas](../polizas/README.md) para reglas de vigencia, persistencia,
contrato y activacion principal.

POST y GET devuelven `200`, `estado: confirmado`, `simulada: true`,
`referenciaInterna: SIM-<UUID>` e `importe: {monto, moneda}`. La referencia es
interna, no bancaria. El reintento recupera el mismo registro incluso con una
nueva clave o después de vencer la oferta/avanzar la saga; no vuelve a modificarla.
Solo la clave de creación queda asociada al registro y usarla en otra solicitud
del mismo cliente devuelve `409 conflicto_idempotencia`. El objeto `saga` del pago
es la instantánea de confirmación; GET suscripción informa cualquier estado posterior.

GET responde `404 pago_no_encontrado` únicamente para una solicitud propia sin pago;
una ajena/inexistente responde `404 no_encontrado`. POST sin suscripción devuelve
404; una no aprobada, selección inválida, oferta vencida o saga no pendiente devuelve
`422 pago_no_permitido`. Un fallo de persistencia revierte todo y nunca se informa éxito.
El contrato público y códigos del BFF están en `contracts/openapi/bff-web-v1.yaml`.

El listado y el detalle incluyen coberturas, condiciones y vigencia para los ocho seguros,
incluido `parametric-weather` (lluvia intensa o sequía, según el evento declarado).
El detalle de un producto inexistente devuelve `404`.

Ejemplo:

```bash
curl -X POST localhost:5000/v1/cotizaciones -H 'Content-Type: application/json' -d '{
  "clienteId": "8f1c2a4e-1111-4c1e-9b1a-000000000001",
  "productoId": "11111111-1111-4111-8111-111111111111",
  "perfilId": "perfil-1",
  "moneda": "COP",
  "coberturas": [
    {"coberturaId": "8f1c2a4e-3333-4c1e-9b1a-000000000003", "sumaAsegurada": "2000000", "deducible": "200000"}
  ]
}'
```

Respuestas: `201` cotización creada, `400` solicitud inválida, `422` regla de negocio violada
(incluye producto sin regla de tarifa vigente o perfil inexistente) y `500` error controlado.
Toda respuesta lleva `X-Correlation-Id` (se reutiliza el recibido o se genera uno); los errores
también lo incluyen en el cuerpo como `correlationId`. Si el cálculo falla no se persiste nada.

La respuesta incluye `decision` con la `reglaVersion`, el `perfilId` y el `hashDecision` usados.
Mismas entradas y misma versión de reglas producen la misma prima y el mismo `hashDecision`.

La creación de solicitudes requiere el encabezado `Idempotency-Key` con un UUID. La misma
clave y los mismos datos devuelven el registro original; reutilizarla con datos diferentes
produce `409`.

## Solicitudes multirramo

El contrato público está en `contracts/openapi/bff-web-v1.yaml`. El BFF recibe exclusivamente
`{productoId, datosProducto}` y toma la identidad y el contacto del usuario autenticado mediante
`Authorization: Bearer`. El servicio interno recibe además `clienteId`, `tipoCliente`,
`tipoDocumento`, `identificacion`, `correo` y `telefono`; sus consultas usan `clienteId`.
No se admiten campos adicionales ni datos de otro producto:

| productoId | datosProducto (todos sus valores son strings) |
|------------|----------------------------------------------|
| `automotive` | `vehiclePlate` |
| `travel` | `destinationCountryCode`, `departureDate`, `returnDate` |
| `life` | `birthDate` |
| `device-protection` | `deviceType`, `brandModel`, `purchaseValue` |
| `parametric` | `airline`, `flightNumber`, `flightDate`, opcional `coverageType` |
| `parametric-weather` | `location`, `weatherEvent`, `startDate`, `endDate`, `insuredAmount` |
| `payment-protection` | `creditType`, `monthlyPayment` |
| `mortgage-life` | `birthDate`, `mortgageBalance` |

`coverageType` admite `delay-cancellation` (predeterminado si se omite), `missed-connection`
y `comprehensive`. Las solicitudes antiguas conservan sus datos sin añadir este campo.
`flightDate` debe ser posterior a hoy al crear. Las ofertas para conexión siempre incluyen
pérdida de conexión por afectación del vuelo previo atribuible a la aerolínea, sujeta a las
condiciones del plan; no cubren llegar tarde al aeropuerto por responsabilidad del pasajero.

Para clima, `location` es municipio y departamento en texto no vacío de hasta 120 caracteres
(sin DANE ni catálogo externo). `weatherEvent` admite `heavy-rain` o `drought`.
`startDate` y `endDate` son fechas reales `YYYY-MM-DD`, posteriores a hoy al crear, en orden,
con máximo 366 días inclusive. `insuredAmount` es un entero positivo sin separadores,
entre `"1"` y `"1000000000"` COP. La suma asegurada declarada se usa en la cotización
persistida y las ofertas muestran el evento, monto, ubicación y periodo declarados.
Las fechas pasadas no impiden consultar registros históricos.

Todas las ofertas y sus primas son **simuladas**, no propuestas vinculantes de aseguradoras.
Los importes de respuesta conservan `{monto, moneda}` con moneda `COP` en `prima`;
`primaMensual` es `null` para planes por viaje, vuelo o período asegurado.
En clima, la prima ilustrativa por periodo es `max(1000, monto * tasaDiaria * días * factorPlan)`
por el factor de riesgo simulado: tasa diaria `0.0001` para lluvia o `0.00012` para sequía,
y factores de plan `1`, `0.85` y `0.70`. Este cálculo de comparación es independiente
del cálculo genérico versionado de la cotización base descrito abajo. Se conserva
la fórmula previa del clima, sin atribuirle una tasa del modelo académico.
Sus tres planes y el factor de riesgo de comparación se guardan al crear en
`tarificacion.modelo = clima-v1`; las lecturas y selecciones usan esos importes
guardados aunque cambien después el perfil o las reglas. La cotización base sigue
aplicando la regla climática `tarifa-2026.1`, sin cambiar su importe ni fórmula.
El disparador, umbral y compensación quedan sujetos a las condiciones del plan.
No se implementan emision real, monitoreo meteorológico, consultas a aerolíneas, siniestros
ni pagos automáticos o reales.

## Tarificación

### Modelo académico v1

`academico-v1` es una **simulación aprobada para el proyecto académico**, no una fórmula
actuarial validada ni una tarifa comercial de una aseguradora. La migración `academico_v1`
incorpora reglas para los siete productos, vigentes desde el 17 de octubre de 2026. No cambia
las primas ni instantáneas anteriores.

`primaCOP = ROUND_HALF_UP(baseAsegurada * tasaProducto * (1 + 0.20 * score) * factorPlan * duracion)`

Se calcula cada plan desde el importe sin redondear y se redondea una sola vez al **peso COP**.
El transporte y la persistencia monetaria conservan dos decimales (`99000.00`).
El deducible sigue siendo una condición; **no se descuenta** de la base en este modelo, ni
impone un mínimo adicional al valor declarado del dispositivo.

| Producto | Base asegurada | Tasa | Periodicidad / duración |
|----------|----------------|------|-------------------------|
| `automotive` | Valor asegurado del catálogo | `0.0015` | Mensual / 1 |
| `travel` | Límite médico del catálogo | `0.00001` por día | Por viaje / días calendario inclusivos |
| `life` | Capital del catálogo | `0.001` | Mensual / 1 |
| `device-protection` | `purchaseValue` declarado | `0.004` | Mensual / 1 |
| `parametric` | Compensación máxima del catálogo | `0.01` | Por vuelo / 1 |
| `payment-protection` | `6 * monthlyPayment` declarado | `0.005` | Mensual / 1 |
| `mortgage-life` | `mortgageBalance` declarado | `0.0005` | Mensual / 1 |

Los factores de paquetes de beneficios son **Esencial 1.00**, **Integral 1.15** y **Premium 1.30**.
Los tres comparten la base del producto; no representan los límites/deducibles de las antiguas
plantillas comerciales simuladas. La prima estimada de la cotización es la del plan Esencial.
Las modalidades de `parametric.coverageType` cambian las coberturas y condiciones,
no la tasa ni los factores de los planes académicos.
Ejemplo: saldo hipotecario `180000000`, score `0.50`: primas `99000`, `113850`, `128700` COP/mes.
Con saldo `90000000`, el plan Esencial cuesta `49500` COP/mes.

#### Beneficios de los planes nuevos

Las nuevas solicitudes académicas y climáticas guardan `planes[].presentacion` con versión
`beneficios-v1` dentro de la instantánea y su hash. Esencial incluye la primera cobertura
del catálogo, Integral agrega la segunda y Premium la tercera cuando existe.
Todos conservan la misma suma asegurada, deducible, duración y fórmula de precio.
Los campos HTTP `descripcion`, `coberturaPrincipal`, `coberturas`, `condiciones`,
`responsabilidadCivil` y `beneficiosAdicionales` reflejan el paquete guardado.
Responsabilidad civil solo se indica incluida cuando forma parte de ese paquete,
sin inventar un límite monetario.

| Producto | Esencial | Integral agrega | Premium agrega |
|----------|----------|-----------------|----------------|
| Automotriz | Daños al vehículo | Responsabilidad civil | Asistencia en carretera |
| Viaje | Asistencia médica | Equipaje | Demoras y cancelaciones |
| Vida | Fallecimiento | Incapacidad total y permanente | Orientación adicional |
| Dispositivos | Daño accidental | Hurto calificado | Falla funcional |
| Vuelos: retraso/cancelación | Retraso de vuelo | Cancelación | Orientación adicional |
| Vuelos: conexión | Pérdida de conexión | Orientación adicional | Orientación adicional |
| Vuelos: combinado | Pérdida de conexión y cancelación | Retraso de vuelo | Orientación adicional |
| Pagos | Desempleo involuntario | Incapacidad temporal | Orientación adicional |
| Vida hipotecaria | Fallecimiento | Incapacidad total y permanente | Orientación adicional |

Clima conserva sus niveles existentes **Esencial, Plus e Integral** y sus primas.
La cobertura sigue siendo únicamente lluvia intensa o sequía según lo declarado;
los temas de orientación crecen de uno a tres: condiciones, evento elegido y
disparador/umbral del plan, sin inventar umbrales ni compensaciones.

La orientación también es acumulativa: condiciones del plan (Esencial), orientación
sobre la primera cobertura (Integral) y sobre la última del catálogo (Premium).
En conexión exclusivamente, el tercer tema son las exclusiones de conexión.
Son temas de la orientación simulada ya existente, no servicios nuevos de proveedores,
asesoría profesional contratada ni pagos adicionales. Si hay menos de tres coberturas,
no se inventan riesgos, montos, umbrales o servicios para llenar el nivel superior.
La modalidad de vuelo elegida se respeta en todos los niveles.

**Compatibilidad:** una lectura o selección nunca regenera estos beneficios desde el
catálogo actual. Las instantáneas sin `presentacion` mantienen exactamente la plantilla
anterior, incluso si la oferta ya fue aceptada; no hay migración ni reescritura de registros.
Los planes climáticos conservan su modelo independiente, precios, evento único elegido
y condiciones; solo amplían los temas de orientación: no se agrega sequía a una
solicitud de lluvia ni viceversa.
Para ver los paquetes nuevos se debe crear una **nueva solicitud de cotización**
con nueva clave de idempotencia; reintentar la anterior devuelve la decisión original.
Los contratos HTTP y de selección no cambian: la selección identifica la oferta y versión,
y sus beneficios se consultan en el listado persistido de esa misma solicitud.

En solicitudes, se validan nuevamente los datos antes de calcular. Fechas de viaje iguales
cuentan como un día. Edad, país, placa, aerolínea y demás campos declarativos no añaden factores
de precio. Los importes declarados son enteros positivos de hasta 16 dígitos; la base derivada
y las primas deben caber en `Numeric(18,2)` y la prima redondeada debe ser positiva.

`/v1/cotizaciones` continúa aceptando coberturas monetarias explícitas y aplica la regla vigente
a cada una (importe por unidad de tasa, sin inferir duración ni datos de producto). Para calcular
un viaje completo y obtener planes se debe usar `/v1/solicitudes-cotizacion`.
Sin regla vigente, perfil válido o importe calculable se devuelve un error controlado sin
guardar cotización, coberturas, instantánea ni evento parcial.

Cada solicitud nueva guarda datos del producto, base, duración, periodicidad, factores y primas
de los tres planes dentro de la instantánea versionada, junto al score, referencia y versión
del perfil realmente recibido. Las ofertas y la selección leen esos resultados persistidos:
no consultan otra vez el perfil ni recalculan precios con reglas actuales. El reintento con la
misma clave de idempotencia conserva la decisión original incluso si cambian reglas o perfil.

### Perfilamiento e histórico

El proveedor configurado por defecto **sigue siendo simulado**: score fijo en `[0,1]`, donde
un valor mayor implica mayor riesgo. No consulta Open Finance ni burós y **no deriva riesgo
del documento de identidad**. La instantánea identifica la versión `perfil-simulado-1`; un
proveedor sustituido mediante el puerto debe entregar su propia referencia y versión.

Las reglas históricas `tarifa-2026.1` conservan su fórmula
`(sumaAsegurada - deducible) * tasa * (1 + score * factorPerfil)`.
Las solicitudes anteriores sin planes académicos siguen usando el adaptador de ofertas legado;
no se migran ni revalorizan decisiones ya registradas.

- La **regla de tarifa** (`tasa`, `factorPerfil`) es versionada y tiene vigencia (`reglas_tarifa`).
  Se aplica la regla vigente del producto al momento del cálculo; sin regla vigente se rechaza.
  La migración `0003` siembra `tarifa-2026.1` para el seguro automotriz.
  `0005` agrega los otros seis ramos iniciales y `clima_v1` la tarifa simulada climática.
  Las reglas históricas se conservan; las nuevas tienen una fecha de inicio posterior.
- El **perfil de riesgo** lo entrega el puerto `ProveedorPerfil`. Mientras no exista el contexto de
  Perfilamiento se usa `ProveedorPerfilSimulado` (score fijo `COTIZACION_PERFIL_SCORE_SIMULADO`).
- En `/v1/solicitudes-cotizacion` el perfil es el del propio cliente (`perfilId = clienteId`).
- Las reglas se leen con una caché local de `COTIZACION_REGLAS_CACHE_SEGUNDOS` que nunca usa una
  regla que ya no esté vigente.

Las ofertas legadas y la creación de planes climáticos usan el adaptador determinista A/B/C;
las académicas no lo consultan. Clima guarda su factor en la instantánea y no vuelve
a consultar ese adaptador al listar o seleccionar ofertas.

### Contrato de primas y compatibilidad

Ofertas y GET/PUT de selección devuelven `prima: {monto, moneda}` y
`periodicidad: mensual | por_viaje | por_vuelo | por_periodo`. `primaMensual` conserva el importe para
mensualidades y es **null** para viaje/vuelo/período asegurado. El BFF web transmite ambos campos nuevos y
acepta respuestas legadas mensuales cuando faltan ambos.
El POST/GET de solicitud incluye además `cotizacion` con prima estimada, coberturas y decisión.

**La nulabilidad de `primaMensual` no es retrocompatible para consumidores que solo leen ese
campo**: deben desplegarse con un consumidor que lea `prima` y etiquete según `periodicidad`.
No mostrar el importe de un viaje/vuelo/período como mensualidad. La selección mantiene la petición
`{ofertaId, version}` y sus claves de idempotencia; su importe/periodicidad se conservan en BD.

## Base de datos

Las solicitudes, cotizaciones calculadas y coberturas se almacenan en `cotizaciones` y
`coberturas_cotizadas`. Cada cálculo exitoso guarda, en la misma transacción, una instantánea
inmutable en `instantaneas_decision` (entradas, perfil, regla y resultado con sus hashes) y un evento
`InstantaneaDecisionRegistrada` en `outbox_eventos` pendiente de publicar. Las tablas están definidas en `infrastructure/persistence/models.py` y versionadas con
Alembic.

Las revisiones `clima_v1` y `academico_v1` conservan su padre `0005`.
La revisión vacía `clima_academico` une ambas; `clima_suscripciones` une ese resultado
con `0006`, la revisión de suscripciones integrada en `develop`, que permanece intacta.
`alembic upgrade head` aplica las que falten y deja una sola cabeza.
La revisión climática de este PR cambia su identificador anterior `0006` a `clima_v1`
para evitar la colisión con suscripciones, sin cambiar su tarifa.
Una base previa de este PR que todavía registre `0006` debe revisar su historial antes
de actualizar: ese identificador ahora corresponde a suscripciones y no debe
reinterpretarse automáticamente como clima. Respaldar y verificar esquema y reglas
antes de ajustar un historial climático anterior.
Se conservan las ocho reglas `tarifa-2026.1` y las siete reglas `academico-v1`,
además de las primas e instantáneas históricas.
La columna histórica `selecciones_oferta.prima_mensual` se conserva por compatibilidad física:
almacena el importe seleccionado y se interpreta junto a `periodicidad`.
Su precisión se amplía a `Numeric(18,2)`. El downgrade elimina reglas nuevas y periodicidad,
pero no reduce la precisión ni altera decisiones. Antes de volver a una versión anterior,
detener escrituras y respaldar los datos; una versión antigua no conoce planes no mensuales.

```bash
alembic upgrade head                              # aplicar migraciones
alembic revision --autogenerate -m "descripcion"  # nueva migración tras cambiar modelos
alembic downgrade -1                              # revertir la última
```

## Configuración

| Variable                              | Default                   |
|---------------------------------------|---------------------------|
| `DATABASE_URL`                        | `sqlite:///cotizacion.db` |
| `COTIZACION_VIGENCIA_DIAS`            | `30`                      |
| `DB_POOL_SIZE`                        | `10`                      |
| `DB_MAX_OVERFLOW`                     | `10`                      |
| `COTIZACION_REGLAS_CACHE_SEGUNDOS`    | `30`                      |
| `COTIZACION_PERFIL_SCORE_SIMULADO`    | `0.50`                    |

En Aurora PostgreSQL: `DATABASE_URL=postgresql+psycopg://usuario:clave@host:5432/cotizacion` (instalar con el extra `postgres`).

## Desarrollo local

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head
flask --app cotizacion.app run
pytest
```

En PowerShell, desde la raíz del repositorio (entorno con dependencias instaladas):

```powershell
$env:PYTHONPATH = (Resolve-Path services\cotizacion\src).Path
$env:DATABASE_URL = "sqlite:///cotizacion-local.db"
python -m alembic -c services\cotizacion\alembic.ini upgrade head
python -m flask --app cotizacion.app run --host 127.0.0.1 --port 5000
```

Para integrar la web, ejecutar también identidad y BFF Web según sus README; configurar
`QUOTE_SERVICE_URL=http://127.0.0.1:5000`. El servicio de cotización es interno:
el navegador debe llamar al BFF, no enviar un `clienteId` elegido por el cliente.

## Contenedor

```bash
docker build -t cotizacion services/cotizacion
docker run --rm -p 8080:8080 -e DATABASE_URL=... cotizacion
```

La imagen sirve la API con gunicorn en el puerto `8080` (`/ping` para health checks) y corre como
usuario sin privilegios. Las migraciones **no** se ejecutan al arrancar: se aplican con
`alembic upgrade head` como paso previo al despliegue.

## Prueba de carga

`loadtest/cotizacion.js` es un guion de [k6](https://k6.io) para el escenario ASR-ESC-01
(500 → 50.000 cotizaciones por minuto, p95 ≤ 250 ms):

```bash
k6 run -e BASE_URL=https://<api> services/cotizacion/loadtest/cotizacion.js
```
