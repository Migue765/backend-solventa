# Siniestros

## Despliegue en AWS

El procedimiento esta en el [runbook de despliegue](../../deploy/k8s/siniestros/README.md).
`terraform/environments/prod/primary/siniestros.tf` crea el bucket privado de evidencias
y el rol IRSA del ServiceAccount `siniestros`. El servicio sube la evidencia confirmada
con el SDK de AWS (ver [Persistencia y acceso](#persistencia-y-acceso)); el bucket no se
monta como disco. El Deployment siempre configura `SINIESTROS_EVIDENCIAS_BUCKET` y usa
un `emptyDir` de 2 GiB solo como staging de cargas en curso.
`deploy/codebuild/deploy.sh` aplica Service y Deployment completos y se detiene antes de
migrar si el ServiceAccount no tiene la anotacion `eks.amazonaws.com/role-arn`. El
despliegue de BFF Web no cambia su `SINIESTROS_SERVICE_URL`: la activacion de los BFF
es explicita y figura en el runbook. Sin bucket, no usar `emptyDir` ni el filesystem
del contenedor como almacen de evidencias.

La integracion usa `0002_estado_historial` del PR84, sin el historial JSON
alternativo de la rama del PR85. Si se aplico esa migracion alternativa en una
base local, no actualizarla a ciegas: requiere conciliar su revision y sus datos
antes de ejecutar Alembic. No modificar una revision ya aplicada en produccion.

Servicio interno con base de datos propia. Comparte registro, evidencia y consulta
de estado e historial (HU25 Web, SOL-318). HU21 movil reutiliza la misma capacidad
de consulta, sin otro motor de estados. Las transiciones de evaluacion (HU26/HU27)
no forman parte de esta entrega.

## Modelo

Sigue el agregado `Siniestro` del modelo de dominio del documento de arquitectura
(contexto Siniestros, datos propios segun D-02, clasificacion **Restringida**).

- `siniestros`: conserva `id`, `owner`, operacion idempotente, huella, aviso JSON y
  fecha de registro del modelo integrado. Agrega `tipo`, `estado` y `actualizado_en`.
  `tipo` es `asistido`, `parametrico` o `null`; el registro actual no clasifica
  siniestros y no se deduce esta clasificacion del tipo de evento o de seguro.
- `historial_estados_siniestro`: cambios por `secuencia`, con `estado`, `ocurrido_en`
  y `visible_cliente`.

La consulta de estado no expone decisiones internas, evaluacion de fraude ni
evidencias. Estas ultimas conservan sus rutas autorizadas independientes.

La migracion `0001_siniestros` permanece intacta. `0002_estado_historial` agrega
columnas e historial sin recrear la tabla ni las cargas. Para avisos existentes
registra el reporte inicial con la fecha durable `created`, sin inventar avances
ni clasificacion. Cada nuevo aviso guarda ese cambio en la misma transaccion;
un replay no duplica el historial ni modifica su fecha.
`0003_resultados_operacion` agrega UUID de operacion, huella y estado HTTP a la
auditoria, con indice para recuperar resultados. Conserva las filas anteriores con
campos nuevos null y no cambia avisos, cargas ni archivos.

El acuse de registro conserva `estado=registrado` para web y movil. La consulta
de avance devuelve el estado de dominio, inicialmente `reportado`, y su historial.
Un acuse no sustituye una consulta del estado vigente.

Estados: `reportado`, `en_evaluacion`, `en_revision`, `en_inspeccion`, `aprobado`,
`rechazado`, `pagado`, `cerrado`. La descripcion que ve el cliente sale de un catalogo
del dominio (`domain/estado.py`); no se guarda ni expone texto interno.

## Historial permitido e integridad

El historial devuelto contiene los cambios con `visible_cliente=true` y siempre el cambio
que fijo el estado vigente. Antes de responder se verifica que el historial no este vacio,
use un tipo conocido o `null`, tenga `ocurrido_en <= reportado_en`,
inicie con `reportado` en `reportado_en`, sea cronologico, use estados conocidos y que
su ultimo cambio coincida con `estado` y `actualizado_en`. Si algo no cuadra responde
`503 siniestro_no_integro`: un registro incoherente nunca se presenta como estado valido.

## API interna

Exige el `INTERNAL_API_TOKEN` como Bearer y `X-Cliente-ID` derivado por el BFF
de la sesion autenticada. No acepta `clienteId` en la URL.

`GET /v1/siniestros/{id}/estado` devuelve
`{id,polizaId,tipo,estado,descripcion,ocurridoEn,reportadoEn,actualizadoEn,historial:[{estado,descripcion,ocurridoEn}]}`.
La busqueda filtra por id **y** cliente, asi que un siniestro inexistente y uno de otro
cliente responden igual: `404 siniestro_no_encontrado`. Parametros invalidos: `400`;
credencial ausente o incorrecta: `401`; fallo de persistencia: `503`. Todas las
respuestas llevan `Cache-Control: no-store` y `X-Correlation-ID`.

## Prueba local con la Web (SOL-177)

Usa el entorno `.venv` de la raiz con identidad, cotizacion, siniestros y bff-web
instalados (`pip install -e`). Abre una consola de PowerShell por servicio, todas en la
raiz del backend, y define en **cada una** la misma credencial interna local:

```powershell
$env:INTERNAL_API_TOKEN = "<credencial local, no produccion>"
```

1. Identidad: sigue los pasos de [bff-web](../bff-web/README.md#integración-local-en-powershell)
   en el puerto `15003`.
2. Cotizacion (solo para que el tablero de inicio cargue): igual que en bff-web, puerto `15000`.
3. Siniestros:

   ```powershell
   $env:SINIESTROS_DATABASE_URL = "sqlite:///siniestros-local.db"
   $env:SINIESTROS_STORAGE_ROOT = Join-Path (Get-Location) "evidencias-locales"
   $env:POLIZAS_SERVICE_URL = "http://127.0.0.1:15004"
   .\.venv\Scripts\python -m alembic -c services\siniestros\alembic.ini upgrade head
   .\.venv\Scripts\python -m flask --app siniestros.app run --host 127.0.0.1 --port 15005
   ```

4. BFF Web en el puerto `5001`, que es el destino de `proxy.conf.json` de la Web
   (detén antes cualquier BFF anterior que ocupe ese puerto):

   ```powershell
   $env:PYTHONPATH = (Resolve-Path services\bff-web\src).Path
   $env:IDENTIDAD_URL = "http://127.0.0.1:15003"
   $env:JWT_ISSUER = "http://127.0.0.1:15003"
   $env:QUOTE_SERVICE_URL = "http://127.0.0.1:15000"
   $env:SINIESTROS_SERVICE_URL = "http://127.0.0.1:15005"
   .\.venv\Scripts\python -m flask --app bff_web.app run --host 127.0.0.1 --port 5001
   ```

5. Para explorar estados aun sin flujo de evaluacion, registra una cuenta,
   obtén su id y siembra tres siniestros sinteticos
   (reportado, en inspeccion con un paso interno oculto, y parametrico pagado):

   ```powershell
   $cuenta = @{ nombre = "Cliente Local"; correo = "cliente.local@solventa.co"; contrasena = Read-Host "Contrasena (min. 8)" }
   Invoke-RestMethod -Method Post -Uri http://127.0.0.1:5001/v1/usuarios -ContentType "application/json" -Body ($cuenta | ConvertTo-Json)
   $sesion = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:5001/v1/sesiones -ContentType "application/json" -Body (@{ correo = $cuenta.correo; contrasena = $cuenta.contrasena } | ConvertTo-Json)
   $env:SINIESTROS_DATABASE_URL = "sqlite:///siniestros-local.db"
   $env:SINIESTROS_STORAGE_ROOT = Join-Path (Get-Location) "evidencias-locales"
   $env:POLIZAS_SERVICE_URL = "http://127.0.0.1:15004"
   .\.venv\Scripts\python services\siniestros\scripts\sembrar_local.py --cliente-id $sesion.usuario.id
   ```

   El script imprime los numeros de siniestro y solo acepta bases SQLite.

6. En `solventa-web-pf2`, `pnpm start:backend`, abre `http://localhost:4200`, inicia
   sesion con esa cuenta y entra a **Siniestros**; pega uno de los numeros impresos.
   Para ver el caso no disponible usa un UUID cualquiera; para ver el error recuperable
   detén Siniestros y pulsa **Actualizar**.

## Ejecucion y validacion

Desde la raiz, en PowerShell:

```powershell
python -m pip install -e ".\services\siniestros[dev]"
$env:SINIESTROS_DATABASE_URL = "sqlite:///siniestros-local.db"
$env:INTERNAL_API_TOKEN = "<credencial local, no produccion>"
python -m alembic -c services\siniestros\alembic.ini upgrade head
python -m flask --app siniestros.app run --port 5005
```

En el BFF Web usar `SINIESTROS_SERVICE_URL=http://127.0.0.1:5005` y la misma credencial.

`python -m pytest services\siniestros\tests tests\test_claims_flow.py` verifica consulta,
historial permitido, aislamiento entre clientes, inexistencia, registros incoherentes,
fallo de persistencia, contrato OpenAPI y latencia. La latencia se mide con 200 consultas
en proceso y extremo a extremo (BFF -> HTTP -> Siniestros sobre SQLite) contra
p95 <= 150 ms y p99 <= 300 ms. Es una linea base local, no acredita el SLA desplegado.
## Registro y evidencia

Servicio compartido para web y movil. Conserva siniestros, operaciones idempotentes,
auditoria y referencias de archivos privados. Los BFF no almacenan soportes.
Contrato de cliente: `contracts/openapi/siniestros-v1.yaml`, prefijos `/v1` y
`/movil/v1`. El registro conserva el contrato del PR web SOL-175.

## Persistencia y acceso

El servicio exige `INTERNAL_API_TOKEN` y `X-Cliente-ID` en cada operacion.
Solo los BFF construyen esta cabecera despues de verificar firma RS256, canal,
rol y scope `cliente`; no aceptan un cliente o autor del formulario. La red del
servicio debe permanecer privada. Siniestros consulta las polizas reales por
HTTP con credencial interna y comprueba pertenencia, vigencia y fecha del evento.
Un objeto ajeno recibe el mismo 404 que uno inexistente.

`SINIESTROS_DATABASE_URL` es obligatorio. Hay migracion Alembic para SQLite y
PostgreSQL; no hay base en memoria ni fallback automatico. El arranque no crea
tablas: ejecutar la migracion antes de servir.

`SINIESTROS_STORAGE_ROOT` es un directorio absoluto privado, fuera de las raices
HTTP, donde se reciben los fragmentos. Los archivos usan UUID del servidor, nunca
nombres del cliente. El proceso debe ser su unico escritor. En POSIX se crean
directorio/archivos con modos 700/600; en Windows el operador debe restringir la
ACL al usuario del servicio. Todos los workers deben compartir la misma base y el
mismo directorio. No compartir el directorio por HTTP.

El almacen durable de la evidencia confirmada depende de `SINIESTROS_EVIDENCIAS_BUCKET`:

- Sin bucket (por defecto), el directorio conserva la evidencia confirmada y debe
  ser un volumen durable. No usar almacenamiento efimero de contenedor.
- Con bucket, la confirmacion sube el archivo validado a
  `evidencias/{siniestroId}/{cargaId}` con `x-amz-checksum-sha256` del SHA256
  declarado, antes del commit: S3 rechaza bytes distintos y la carga solo queda
  `confirmada` si existe la copia remota. Despues del commit se borra la copia
  local y el directorio queda como staging (admite `emptyDir`). La descarga copia
  el objeto a un temporal anonimo del directorio, verifica longitud y SHA256 y lo
  borra al cerrar la respuesta. Cancelar o rechazar bytes borra primero el objeto
  y despues el archivo local.
- Un fallo de S3 responde 503 con `Retry-After`; la carga sigue `pendiente` con su
  offset y el cliente reintenta la misma operacion. Si S3 acepto la copia y fallo
  el commit, el reintento sobrescribe el mismo objeto y la cancelacion lo elimina.
- Credenciales: cadena estandar del SDK de AWS (IRSA en EKS); `AWS_REGION` fija la
  region. Permisos minimos: `s3:PutObject`, `s3:GetObject` y `s3:DeleteObject` sobre
  `arn:aws:s3:::<bucket>/evidencias/*`, mas `kms:GenerateDataKey` y `kms:Decrypt`
  si el bucket cifra con una CMK.
- El bucket debe ser privado (bloqueo de acceso publico, propietario forzado y solo
  TLS), cifrado y versionado; el versionado recupera sobrescrituras o borrados
  accidentales. En AWS, las versiones previas se eliminan definitivamente a los 7
  dias. No usar Object Lock: impediria borrar evidencia cancelada, rechazada
  o cuya supresion se solicite por habeas data.
- El staging es local al pod. Si el pod se reinicia, o una carga cambia de pod
  durante un despliegue, se pierden los fragmentos recibidos: el siguiente fragmento
  o la confirmacion responden 503 y `GET .../cargas/{cargaId}` reinicia el offset a
  cero (`reinicio_requerido`). Mantener una sola replica.

La implementacion inicial serializa escrituras: `BEGIN IMMEDIATE` en SQLite y
advisory lock transaccional en PostgreSQL. Cuota, clave idempotente y archivo quedan
protegidos entre procesos, a cambio de limitar el paralelismo de cargas. Con bucket,
la confirmacion tambien sube a S3 dentro de esa seccion, como ya ocurre con la
validacion de formato; el cliente S3 usa 3 s de conexion, 10 s de lectura y 2
intentos para responder 503 antes del timeout de 30 s del worker. Esta
eleccion es explicita; la validacion local usa SQLite real y un cliente S3 simulado.
No se ha certificado PostgreSQL, S3 real, failover, rendimiento, volumen distribuido
ni durabilidad ante corte electrico en Windows. PostgreSQL y S3 real quedan
pendientes de la aceptacion del runbook de despliegue.

## Protocolo de carga

1. Consultar politica y seleccionar un siniestro del listado autorizado.
2. Persistir UUID de operacion y cuerpo antes de `POST /siniestros/{id}/cargas`.
3. Enviar fragmentos raw con `PUT .../cargas/{cargaId}/contenido`,
   `Content-Length` y `Upload-Offset`. Se leen bloques de 64 KiB, sin cargar el
   archivo completo en memoria. El BFF tambien transmite el stream por bloques.
4. Consultar `GET .../cargas/{cargaId}` despues de reinicio/interrupcion/ACK perdido.
   Solo el offset del servidor autoriza continuar. Reenviar un fragmento antiguo
   produce 409, no una segunda copia.
5. Confirmar con `POST .../cargas/{cargaId}/confirmacion`, sin cuerpo.
   Solo `estado=confirmada` y el listado de evidencias acreditan recepcion.

La misma clave y cuerpo recuperan la misma carga (201 nueva, 200 replay).
Otro cuerpo o siniestro con la misma clave produce `idempotencia_conflictiva`.
La confirmacion es idempotente por carga. La cuota incluye pendientes y
confirmadas; cancelar una pendiente libera su plaza. El backend no expira ni
borra evidencia confirmada por la ruta de cancelacion.

Cada fragmento hace `flush`/`fsync` antes del commit SQL. Una caida antes del
commit puede dejar una cola de bytes no confirmada: el siguiente fragmento la
trunca al offset durable. Si una cancelacion o rechazo elimina el archivo y
falla el commit, GET reconcilia la carga pendiente a offset cero y registra
`reinicio_requerido`. No convierte ese estado en confirmacion ni borra un
soporte confirmado. Un fallo de base/almacenamiento produce 503.

La confirmacion exige tamaño exacto, SHA256 y formato decodificable. Los rechazos
de formato, integridad o ubicacion eliminan bytes invalidos y reinician offset.
Descargar un soporte vuelve a comprobar longitud/hash y exige propiedad.
La respuesta usa attachment, `no-store` y `nosniff`; no entrega URLs publicas.

## Politica inicial configurable

Estos valores son decisiones iniciales de implementacion, no cifras atribuidas a Jira.
El cliente obtiene los valores activos con `GET /siniestros/configuracion-evidencias`.

| Variable | Inicial |
|---|---:|
| `EVIDENCIA_MAX_CANTIDAD` | 10 por siniestro, incluyendo pendientes |
| `EVIDENCIA_MAX_CHUNK_BYTES` | 1048576; maximo admitido por ambos BFF |
| `EVIDENCIA_MAX_FOTO_BYTES` | 10485760 |
| `EVIDENCIA_MAX_DOCUMENTO_BYTES` | 20971520 |
| `EVIDENCIA_MAX_VIDEO_BYTES` | 104857600 |
| `EVIDENCIA_MAX_VIDEO_SECONDS` | 120 |
| `EVIDENCIA_MIME_PERMITIDOS` | `image/jpeg,image/png,application/pdf,video/mp4` |

La lista MIME se puede restringir a un subconjunto no vacio de formatos soportados;
un tipo sin validador o un valor vacio impide arrancar. Nuevos fragmentos y confirmaciones
pendientes vuelven a verificar la politica actual; las evidencias ya confirmadas se conservan.

Formatos soportados: JPEG, PNG, PDF y MP4. Pillow verifica y decodifica fotos
(una imagen, hasta 40 millones de pixeles); pypdf exige PDF legible no cifrado,
sin archivos adjuntos y de 1 a 500 paginas; PyAV exige MP4 con una pista de video decodificable, hasta
3840x2160 y un presupuesto de 60 cuadros por segundo de duracion maxima.
No se ejecutan binarios CLI descargados ni servicios externos de analisis.
Estos controles no sustituyen un antivirus ni detectan contenido geografico
escrito o fotografiado dentro del documento.

`fechaCaptura` es opcional, de origen cliente, ISO con zona; `creadaEn` siempre
lo registra el servidor. `ubicacion` requiere consentimiento booleano `true`,
coordenadas en rango y `fecha` ISO con zona. Fechas de captura/ubicacion futuras
mas de cinco minutos se rechazan. El backend rechaza GPS EXIF, bloques IPTC
Photoshop, claves GPS/location de PNG/XMP/MP4/PDF y GeoPDF sin consentimiento.
El cliente debe quitar metadata geografica antes de calcular el hash cuando no
tiene permiso. No se recomprime ni cambia el hash silenciosamente.

## HU30: eventos parametricos

HU30 es una capacidad exclusivamente backend; no agrega portal ni pantalla web. El
contrato interno esta en
`contracts/openapi/eventos-parametricos-internal-v1.yaml`.

La recepcion acepta lotes de hasta 5.000 eventos en
`POST /internal/v1/eventos-parametricos/recepcion`. Cada fuente debe estar en el objeto
JSON `FUENTES_PARAMETRICAS_JSON` y presentar su `X-Fuente-ID` y Bearer propio. Se valida
el lote completo antes de escribir. La identidad durable es fuente + `eventoId`; repetir
la misma huella cuenta como duplicado y otra huella responde `409 evento_conflictivo`.

`estado_cola_parametrica` conserva el numero exacto de pendientes dentro de la misma
transaccion que inserta o procesa eventos. Si aceptar el lote superaria
`EVENTOS_PARAMETRICOS_CAPACIDAD` (1.100.000 por defecto), responde `429 backpressure`
con `Retry-After` y no acepta parcialmente. Los eventos ya aceptados permanecen en SQL.

Las reglas son versiones inmutables y se registran por
`POST /internal/v1/reglas-parametricas`. Para cada evento se elige la version numerica
mas alta vigente de cada regla compatible con fuente, tipo, metrica, unidad y ubicacion.
La decision durable es `valido`, `no_valido` o `sin_regla_vigente`; conserva evento,
fuente, regla/version cuando aplica y fecha del servidor.

Un resultado valido crea, en la misma transaccion, un siniestro parametrico aprobado y
un caso estable en `casos_pago_automatico`. HU31 consulta la outbox mediante
`POST /internal/v1/casos-pago-automatico/pendientes` y confirma por UUID. Repetir una
consulta o confirmacion no crea otro siniestro ni caso. Un caso pendiente significa
**entrega durable para HU31**, no pago ejecutado.

El trabajador continuo se inicia por separado:

```powershell
python -m siniestros.parametric_worker --limit 5000
```

El criterio de 1.000.000 de eventos en diez minutos queda pendiente de una prueba de
capacidad por HTTP contra los pods y PostgreSQL del ambiente AWS, con limites reales de
CPU/memoria, productores concurrentes, replicas del trabajador y metricas de
backpressure. No se sustituye esa evidencia con una extrapolacion o benchmark SQLite
local. El despliegue en AWS no incluye el trabajador ni fuentes parametricas
configuradas, y no acredita este criterio.

Variables:

- `FUENTES_PARAMETRICAS_JSON`: objeto fuente -> secreto; no se registra ni se devuelve.
- `EVENTOS_PARAMETRICOS_CAPACIDAD`: maximo de aceptados pendientes.
- `EVENTOS_PARAMETRICOS_LOTE`: lote del trabajador, maximo 5.000.
- `EVENTOS_PARAMETRICOS_MAX_BODY_BYTES`: limite del cuerpo de recepcion.

## Errores

| HTTP | `code` |
|---|---|
| 400 | `solicitud_invalida`, `carga_interrumpida` |
| 401 | `sesion_invalida` (BFF), `no_autorizado` (servicio interno), `fuente_no_autorizada` |
| 403 | `alcance_insuficiente` |
| 404 | `siniestro_no_encontrado`, `poliza_no_encontrada`, `carga_no_encontrada`, `evidencia_no_encontrada` |
| 409 | `idempotencia_conflictiva`, `evento_conflictivo`, `version_regla_conflictiva`, `confirmacion_conflictiva`, `offset_conflictivo`, `carga_incompleta`, `carga_cancelada`, `carga_confirmada`, `limite_cantidad` |
| 411 | `longitud_requerida` |
| 413 | `limite_tamano` |
| 415 | `formato_no_permitido` |
| 422 | `integridad_invalida`, `formato_invalido`, `ubicacion_no_autorizada`, `video_fuera_limite`, `fecha_evento_invalida`, `vigencia_invalida`, `fecha_entrega_invalida` |
| 429 | `backpressure` |
| 503 | `servicio_no_disponible` |

Errores usan `application/problem+json` y `correlationId`. La auditoria SQL guarda
actor UUID, accion, resultado, fecha y correlacion; para sincronizacion agrega UUID
de operacion, huella y estado HTTP, sin cuerpos, archivos, credenciales ni coordenadas.
Rechazos sin acceso a SQL se registran en el log sin
parametros SQL ni datos del cuerpo.

## Ejecucion local

Instalar este proyecto junto con Identidad, Polizas y ambos BFF en un entorno
virtual propio. Configurar `SINIESTROS_DATABASE_URL`, `SINIESTROS_STORAGE_ROOT`,
`INTERNAL_API_TOKEN` y `POLIZAS_SERVICE_URL`. `SINIESTROS_EVIDENCIAS_BUCKET` es
opcional; sin el, la evidencia confirmada queda en el disco local. Luego:

```powershell
python -m alembic -c services\siniestros\alembic.ini upgrade head
python -m flask --app siniestros.app run --host 127.0.0.1 --port <puerto-propio>
```

En cada BFF configurar `SINIESTROS_SERVICE_URL` e `INTERNAL_API_TOKEN`. Sin estas
variables el resto de rutas conserva su comportamiento y siniestros devuelve 503.

Para comprobacion integrada reproducible sin credenciales externas:

```powershell
python -m tests.claims_local_stack --directory <directorio-absoluto-nuevo> --state <archivo-privado-nuevo>
python -m pytest tests\test_claims_evidence_flow.py services\siniestros\tests
```

El stack utiliza servidores HTTP en puertos efimeros de loopback, migraciones,
bases reales y archivos privados. Registra dos cuentas sinteticas por API, inicia
sesion, emite polizas por la API interna autorizada y crea siniestros por el BFF.
La emision de polizas sigue siendo la simulada existente del producto; no representa
un pago real. El archivo temporal contiene credenciales sinteticas de esa ejecucion:
no imprimirlo, publicarlo ni conservarlo al finalizar. Detener solo este proceso.

## Sincronizacion movil: SOL-193 / SOL-237 / SOL-328

Las operaciones moviles disponibles son registro de siniestro e inicio, fragmentos
y confirmacion de evidencia. No hay comandos moviles de comentarios o transiciones:
sus criterios de deduplicacion no se acreditan con estas pruebas ni se implementan
operaciones nuevas para cubrirlos. La consulta de estado es de solo lectura.

Se reutilizan los endpoints anteriores, sin lote ni framework de sincronizacion.
La identidad de una operacion es cliente autenticado, tipo de operacion y UUID
`Idempotency-Key`; la huella conserva el cuerpo normalizado y, para cargas, el siniestro.
La misma clave nunca reemplaza un cuerpo aceptado. La pertenencia se verifica en cada
intento; una carga ajena no se recupera con conocer su UUID. El orden de envio de
operaciones pendientes es responsabilidad de la cola local, no del UUID.

| Resultado observado | Recuperacion con el contrato existente |
|---|---|
| Registro 201 o replay 200 | Conservar id y `registradoEn`; ACK perdido se recupera repitiendo clave y cuerpo |
| Inicio de carga 201 o replay 200 | Conservar id de carga; `pendiente` no acredita evidencia recibida |
| Carga `confirmada` | ACK perdido se recupera por GET o repeticion de confirmacion; `creadaEn` esta en el listado de evidencias |
| 409 `idempotencia_conflictiva` | Conflicto: conservar entrada local y resultado central, sin sobrescribir ni renovar UUID automaticamente |
| 409 `offset_conflictivo` o `carga_incompleta` | Reconciliar GET de la carga; no tratar todo 409 como conflicto de contenido ni confirmar bytes faltantes |
| 409 `carga_cancelada` | La operacion original sigue cancelada; no crear otra implicitamente |
| 400/403/404/413/415/422 u otro rechazo de negocio | Conservar motivo `code`; no declarar recepcion ni convertirlo en exito |
| 401 | Recuperar sesion del mismo usuario antes de continuar; no cambiar propietario de la entrada |
| Fallo de transporte o 503 | Resultado desconocido/recuperable: consultar o repetir con la misma identidad, nunca generar otra por perder el ACK |

Con UUID y cuerpo validos, los rechazos de negocio de registro/inicio quedan
fijos para esa operacion, incluso si luego cambia la cuota o politica. Una correccion
requiere una operacion nueva decidida por el usuario; el backend no renueva IDs.
Los rechazos de autenticacion/autorizacion, solicitudes malformadas y fallos 503
no fijan resultado. En inicio se comprueba propiedad antes de recuperar resultado;
un siniestro ajeno o inexistente sigue devolviendo 404 sin revelar auditoria.
El registro trata `poliza_no_encontrada` como rechazo de negocio de la solicitud.

Registro, inicio, GET carga, fragmento, confirmacion y errores de negocio vinculados
incluyen el objeto aditivo:

```json
{
  "ultimoResultado": {
    "operacionId": "UUID local",
    "fecha": "2026-11-05T20:00:00+00:00",
    "httpStatus": 422,
    "code": "integridad_invalida"
  }
}
```

`code=null` indica exito. `httpStatus` describe el resultado original: en un replay
HTTP 200 de registro puede ser 201. `fecha` conserva el instante persistido,
sin actualizarlo por releer/repetir. Un conflicto de huella devuelve su propio
409/codigo/fecha; no reemplaza el resultado del cuerpo original. El cliente conserva
por separado la fecha local de observacion y esta fecha central.

No hay endpoint nuevo: repetir POST con UUID/cuerpo originales recupera aceptacion
o rechazo. GET carga recupera el ultimo rechazo/exito de fragmento o confirmacion
si se perdio el ACK. Los rechazos de fase no fijan un rechazo final: una incompleta
puede seguir desde el offset central y una integridad invalida permite reenviar
bytes correctos con el hash original. Cada fase posterior guarda su propio resultado.
Una confirmada devuelve su resultado de confirmacion aunque despues se rechace
un PUT o DELETE. Cancelar dos veces no cambia su fecha. Para canceladas legadas sin
fecha atribuible, `ultimoResultado=null`; pendientes/confirmadas legadas usan la
fecha durable de creacion/confirmacion ya existente.

El servicio persiste el rechazo y su huella dentro del lock/transaccion de escritura,
y responde despues del commit. Repeticiones concurrentes recuperan un resultado.
Si falla auditoria o commit responde 503 sin resultado durable; no confirma ni guarda
un resultado ficticio. Una falla de SQL tampoco congela una operacion para el futuro.

El replay recupera una carga ya aceptada aunque su MIME haya sido deshabilitado
despues; enviar nuevos fragmentos o confirmar una pendiente sigue validando la
politica actual. Una carga confirmada no se elimina ni se vuelve pendiente por ese cambio.
Cambiar un cuerpo valido con la misma clave produce conflicto antes de aplicar
la politica de nuevas cargas; entradas malformadas conservan su rechazo de validacion.

**Politica de estado central:** registro y anexos no son reemplazos del agregado.
Un replay de registro conserva el acuse original y no restaura `reportado`, no altera
`actualizado_en` ni duplica historial si el servidor avanzo a `en_evaluacion`.
La respuesta `estado=registrado` es el acuse, no la version vigente. Inicio nuevo,
fragmentos y confirmaciones pendientes validan la integridad del agregado e historial
con la misma Session y dentro del lock. Una incoherencia devuelve el codigo existente
`503 siniestro_no_integro`; no modifica bytes ni fija rechazo final. Los resultados
aceptados siguen recuperables, comprobando antes la autorizacion vigente.
No se agrega version esperada/If-Match ni terminalidad por estado nominal:
las operaciones disponibles no editan el agregado y el dominio no define una matriz
que prohiba anexos segun estado. Esta es la decision tecnica acordada para el alcance,
no una afirmacion de que existe control optimista de versiones.

Cada carga conserva su resultado por separado: una falla deja intactas las evidencias
aceptadas; una carga parcial o no enviada no aparece como confirmada. La prueba
`test_partial_queue_preserves_accepted_and_unprocessed_operations` lo verifica tras
recrear la aplicacion, con reanudacion por offset y sin duplicados.

Las fechas de evento/captura no son fechas de sincronizacion. `registradoEn` y
`creadaEn` son fechas centrales de aceptacion. La auditoria vincula fecha, actor,
accion, UUID, huella, estado HTTP y motivo; POST/GET recuperan solo el resultado
autorizado, no exponen el diario ni cuerpos. La cola conserva tambien su fecha local
de observacion; no usa evento/captura como fecha de resultado.

### Regresiones aisladas

Desde la raiz, con las dependencias de desarrollo ya instaladas:

```powershell
python -m pytest tests\test_claims_evidence_flow.py tests\test_claims_sync_results.py services\siniestros\tests --basetemp=reports\sol328-pytest -q -p no:cacheprovider
```

`--basetemp` debe ser un directorio dedicado a esa ejecucion: pytest borra su contenido.
La fixture `runtime` reutiliza `tests.claims_local_stack.stack`: migra SQLite bajo
ese directorio, genera claves y cuentas sinteticas y sirve Identidad, Polizas,
Siniestros y ambos BFF en puertos efimeros de loopback. `ExitStack` apaga los servidores
al finalizar; no se ejecuta el modo CLI persistente ni se usan bases productivas.
Los casos cubren colision de cuerpo, rechazo durable, ACK perdido, reinicio, fallo parcial,
integridad central, migracion y falla de auditoria SQL, ademas de las regresiones de
propiedad, concurrencia y archivos.
Eliminar el directorio dedicado despues de ejecutar. Esta evidencia local no certifica
PostgreSQL, infraestructura durable, despliegue (SOL-315) ni cierra por si sola SOL-328.

### Integracion fisica con HTTP real

El 2026-11-05 el coordinador ejecuto `OfflineQueueHttpDeviceTest` en un Samsung
SM-A217M contra el stack local: **1 test aprobado en 4.287 s**. La prueba uso
cuentas sinteticas, SQLite migrado y las APIs reales de autenticacion, registro
y evidencia. Verifico rechazo durable de inicio400, replay con fecha original,
colision de UUID, continuacion de otro item y retirada sin carga remota.

El proxy acotado corto la conexion despues de persistir una confirmacion200 y
un rechazo422 `integridad_invalida`. Las consultas GET recuperaron los UUID,
fechas, estados HTTP y codigos originales. El reporte registro `http.passed=true`,
sin errores, y `cleanupPassed=true`. El coordinador comprobo que no quedaron
directorio de ejecucion, fixture privada ni reverse18731; no se borraron datos
previos de la aplicacion.

Evidencia local saneada, excluida de Git:
`reports/sol193-http-002db18e-6e5a-4235-a6dc-6bcf133f0d21.json` y su archivo `.log`.
En la validacion final del coordinador tambien pasaron 131 pruebas backend
en 120.41 s, Ruff y mypy de 10 fuentes; la suite fisica UI/cola/reconexion
aprobo 4 de 4 pruebas en 19.061 s. Son comprobaciones distintas de la prueba HTTP.
Esta evidencia acredita la integracion fisica con el entorno local sintetico;
no acredita PostgreSQL, almacenamiento productivo, SLA ni despliegue.
