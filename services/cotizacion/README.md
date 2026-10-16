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
| POST   | `/v1/solicitudes-cotizacion` | Registra una solicitud idempotente |
| GET    | `/v1/solicitudes-cotizacion/{id}` | Consulta una solicitud del cliente |
| GET | `/v1/solicitudes-cotizacion/{id}/perfilamiento` | Consulta el perfil exacto del calculo |

Al habilitar HU11, todas las rutas excepto `/ping` requieren
`Authorization: Bearer <INTERNAL_API_TOKEN>`. Son endpoints internos: el navegador debe usar
el BFF, que toma al cliente de su sesion.

`COTIZACION_PERFILAMIENTO_HABILITADO=true` activa HU11. El default false preserva el flujo
anterior mientras se despliegan los servicios nuevos; se registra una advertencia al arrancar.
No es un fallback ante errores: una vez habilitado, fallas de Perfilamiento o consentimiento
siempre bloquean la operacion, sin recurrir a los simuladores anteriores.

Ejemplo:

```bash
curl -X POST localhost:5000/v1/cotizaciones -H "Authorization: Bearer $INTERNAL_API_TOKEN" -H 'Content-Type: application/json' -d '{
  "clienteId": "8f1c2a4e-1111-4c1e-9b1a-000000000001",
  "productoId": "11111111-1111-4111-8111-111111111111",
  "perfilId": "<UUID de un perfil vigente generado por Perfilamiento>",
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

## Tarificación

`prima = (sumaAsegurada - deducible) * tasa * (1 + score * factorPerfil)`, por cobertura.

- La **regla de tarifa** (`tasa`, `factorPerfil`) es versionada y tiene vigencia (`reglas_tarifa`).
  Se aplica la regla vigente del producto al momento del cálculo; sin regla vigente se rechaza.
  La migración `0003` siembra `tarifa-2026.1` para el seguro automotriz.
- El **perfil de riesgo** lo entrega el puerto `ProveedorPerfil` mediante HTTP a Perfilamiento.
  La creacion de solicitudes genera un perfil para el cliente y el UUID de la cotizacion.
  El endpoint de bajo nivel `/v1/cotizaciones` consume un perfil existente y autorizado.
- Las reglas se leen con una caché local de `COTIZACION_REGLAS_CACHE_SEGUNDOS` que nunca usa una
  regla que ya no esté vigente.

La comparacion de planes usa el mismo perfil persistido, no un segundo perfil por documento.
Antes de listar o seleccionar ofertas se revalida su consentimiento y vigencia. Las fixtures
de Open Finance/Open Data estan en Perfilamiento; no representan informacion financiera real.
Los adaptadores simulados anteriores solo se inyectan en pruebas de regresion.

La instantanea de decision incluye la evidencia del perfil, fuentes, fechas, version de modelo
y consentimiento. No se recalculan ofertas con un perfil nuevo silenciosamente: ante revocacion,
cambio de autorizacion o vencimiento, se exige una nueva solicitud. La consulta historica de la
solicitud no divulga datos de perfilamiento. Un reintento idempotente devuelve la solicitud anterior,
pero no habilita nuevas consultas ni usos de su perfil.

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
| `PERFILAMIENTO_SERVICE_URL`          | `http://localhost:5003` |
| `INTERNAL_API_TOKEN`                 | sin default utilizable |
| `COTIZACION_PERFILAMIENTO_HABILITADO` | `false` |

`COTIZACION_PERFIL_SCORE_SIMULADO` solo aplica al flujo anterior. Con HU11 habilitada no
reemplaza el perfil remoto ni permite omitir el consentimiento.

En Aurora PostgreSQL: `DATABASE_URL=postgresql+psycopg://usuario:clave@host:5432/cotizacion` (instalar con el extra `postgres`).

## Desarrollo local

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head
flask --app cotizacion.app run
pytest
```

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
