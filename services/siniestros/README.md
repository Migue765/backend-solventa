# Siniestros: SOL-313 y SOL-315

Servicio de registro y consulta, integrado en el BFF Web existente. Usa el
servicio Polizas como autoridad de acceso y vigencia; no lee tablas ajenas ni
crea polizas ficticias. Las polizas actuales son academicas/simuladas: registrar
un aviso no constituye cobertura real, aprobacion ni pago de indemnizacion.

## Flujo y contrato

El cliente inicia sesion por el flujo habitual y consume:

- `POST /v1/siniestros`, con JWT e `Idempotency-Key` UUID, recibe
  `{polizaId, fechaEvento, tipoEvento, descripcion, lugar}`. La fecha es ISO 8601
  con zona; la descripcion tiene de 10 a 5000 caracteres, tipoEvento de 1 a 100
  y lugar de 1 a 300, tras eliminar espacios extremos.
- `GET /v1/siniestros/polizas` devuelve `{polizas: [...]}` vigentes autorizadas,
  con ID, tipo, fechas de vigencia y estado, para el selector Web existente.
- `GET /v1/siniestros` devuelve `{siniestros: [...]}`, con ID, poliza,
  fecha de reporte y estado, ordenados por reporte descendente.
- `GET /v1/siniestros/{id}` devuelve evento, descripcion, lugar, `registradoEn`
  e historial.

Contrato publico: `contracts/openapi/bff-web-v1.yaml`. El BFF exige JWT Web y
rol cliente; deriva `clienteId` del principal y rechaza intentos de indicarlo
por cuerpo o consulta. Las consultas publicas no admiten cuerpo ni parametros.

El registro exige poliza vigente autorizada y evento no futuro dentro de su
vigencia (dias UTC inclusivos). El aviso, actor, fecha, poliza, datos del evento,
hash del contenido e historial inicial `registrado` se confirman en una unica
transaccion antes del `201`. No hay confirmacion si falla la persistencia.
La restriccion unica `(cliente_id, clave_idempotencia)` protege incluso entre
procesos; repetir cuerpo y clave recupera el mismo aviso con `200`. Cambiar contenido
responde `409 conflicto_idempotencia`; poliza no vigente, `409 poliza_no_vigente`.
Datos invalidos o fecha futura/fuera de vigencia responden `400`.
Tras perder una confirmacion, reintentar sin cambiar cuerpo
ni clave. El reintento de un aviso confirmado sigue permitido tras vencer
su poliza, siempre que el cliente conserve acceso.

Listado y detalle verifican el acceso actual en Polizas e incluyen casos de
polizas vencidas autorizadas. Un caso ajeno y uno inexistente devuelven el mismo
`404`. Una lista sin casos es `200` con arreglo vacio; una dependencia, respuesta
invalida o consulta fallida es `503`, nunca un resultado parcial exitoso.
Todas las respuestas de estas rutas llevan `Cache-Control: no-store` y
`X-Correlation-ID`.

El servicio interno conserva las mismas rutas, exige Bearer
`INTERNAL_API_TOKEN`, recibe `clienteId` del BFF en POST y como unico parametro
en GET. No se debe publicar directamente en ALB/API Gateway. No se implementan
en estos tickets cargas de soportes, evaluacion ni transiciones posteriores.
El historial actual contiene la transicion inicial persistida.

## Ejecucion local

Desde la raiz en PowerShell, con Polizas ejecutandose y su base migrada:

```powershell
python -m pip install -e ".\services\siniestros[dev]"
$env:SINIESTROS_DATABASE_URL = "sqlite:///siniestros-local.db"
$env:POLIZAS_SERVICE_URL = "http://127.0.0.1:5004"
$env:INTERNAL_API_TOKEN = "<credencial local compartida con BFF y Polizas>"
python -m alembic -c services\siniestros\alembic.ini upgrade head
python -m flask --app siniestros.app run --port 5005
```

En el BFF existente, configurar `SINIESTROS_SERVICE_URL=http://127.0.0.1:5005`
y la misma credencial interna. Sin configuracion, las rutas responden `503`;
no se simula exito. Cada servicio conserva una base propia.

```powershell
python -m pytest tests\test_claims_flow.py services\siniestros\tests
```

Las pruebas recorren JWT firmado -> BFF -> HTTP Siniestros -> HTTP Polizas ->
bases migradas, cubriendo persistencia, reinicio, idempotencia concurrente,
aislamiento, historial, validaciones y fallos. No sustituyen la aceptacion
desde el frontend desplegado.

## Incorporacion al despliegue principal

No usar entornos preview como solucion final. Los manifiestos de
`deploy/k8s/siniestros` y el pipeline Terraform de produccion incorporan el
servicio interno al mismo cluster. Antes de desplegar:

1. Provisionar una base PostgreSQL propia persistente; no usar SQLite en pods.
2. Crear el namespace y el Secret `siniestros-runtime` mediante el mecanismo
   operativo existente, con `SINIESTROS_DATABASE_URL` e `INTERNAL_API_TOKEN`.
   La credencial debe coincidir con Polizas y `profiling-internal` del BFF.
   No guardar secretos en Git.
3. Aplicar/revisar Terraform y ejecutar el pipeline de Siniestros para migrar
   antes del rollout; el pipeline aplica Service y Deployment internos.
4. Publicar el BFF con `SINIESTROS_SERVICE_URL` apuntando al Service interno.
5. Validar desde la aplicacion autenticada: poliza emitida -> reporte -> mismo
   ID al reintentar -> listado -> detalle e historial; una segunda cuenta no
   debe ver el caso. Ready y `/health` no constituyen esta aceptacion.

Estos cambios preparan el despliegue; no ejecutan Terraform ni modifican
produccion. La creacion de reportes y consultas debe conectarse a las pantallas
Web mediante el contrato publico, no a un servicio aislado.
