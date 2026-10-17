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

| Método | Ruta               | Descripción                    |
|--------|--------------------|--------------------------------|
| GET    | `/ping`            | Health check                   |
| POST   | `/v1/cotizaciones` | Crea y calcula una cotización  |
| GET    | `/v1/productos-seguro` | Lista el catálogo de seguros |
| GET    | `/v1/destinos-viaje` | Lista países de destino admitidos |
| POST   | `/v1/solicitudes-cotizacion` | Registra una solicitud idempotente |
| GET    | `/v1/solicitudes-cotizacion/{id}` | Consulta una solicitud del cliente |
| GET    | `/v1/solicitudes-cotizacion/{id}/ofertas` | Compara ofertas simuladas |
| GET/PUT | `/v1/solicitudes-cotizacion/{id}/seleccion` | Consulta o guarda una selección idempotente |

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
Los importes de respuesta conservan `{monto, moneda}` con moneda `COP`; el nombre
`primaMensual` se mantiene por compatibilidad incluso en planes por vuelo o periodo.
En clima, la prima ilustrativa por periodo es `max(1000, monto * tasaDiaria * días * factorPlan)`
por el factor de riesgo simulado: tasa diaria `0.0001` para lluvia o `0.00012` para sequía,
y factores de plan `1`, `0.85` y `0.70`. Este cálculo de comparación es independiente
del cálculo genérico versionado de la cotización base descrito abajo.
El disparador, umbral y compensación quedan sujetos a las condiciones del plan.
No se implementan emisión, monitoreo meteorológico, consultas a aerolíneas, siniestros
ni pagos automáticos o reales.

## Tarificación

`prima = (sumaAsegurada - deducible) * tasa * (1 + score * factorPerfil)`, por cobertura.

- La **regla de tarifa** (`tasa`, `factorPerfil`) es versionada y tiene vigencia (`reglas_tarifa`).
  Se aplica la regla vigente del producto al momento del cálculo; sin regla vigente se rechaza.
  La migración `0003` siembra `tarifa-2026.1` para el seguro automotriz.
  `0005` agrega los otros seis ramos iniciales y `0006` la tarifa simulada climática.
- El **perfil de riesgo** lo entrega el puerto `ProveedorPerfil`. Mientras no exista el contexto de
  Perfilamiento se usa `ProveedorPerfilSimulado` (score fijo `COTIZACION_PERFIL_SCORE_SIMULADO`).
- En `/v1/solicitudes-cotizacion` el perfil es el del propio cliente (`perfilId = clienteId`).
- Las reglas se leen con una caché local de `COTIZACION_REGLAS_CACHE_SEGUNDOS` que nunca usa una
  regla que ya no esté vigente.

La comparación de planes usa adicionalmente un adaptador simulado y determinista para representar
escenarios A, B y C. Ninguno de los adaptadores consulta un buró ni representa información
financiera real; ambos pueden sustituirse por integraciones externas a través de sus puertos.

## Base de datos

Las solicitudes, cotizaciones calculadas y coberturas se almacenan en `cotizaciones` y
`coberturas_cotizadas`. Cada cálculo exitoso guarda, en la misma transacción, una instantánea
inmutable en `instantaneas_decision` (entradas, perfil, regla y resultado con sus hashes) y un evento
`InstantaneaDecisionRegistrada` en `outbox_eventos` pendiente de publicar. Las tablas están definidas en `infrastructure/persistence/models.py` y versionadas con
Alembic.

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
