# Siniestros y evidencia

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
HTTP. Los archivos usan UUID del servidor, nunca nombres del cliente. El proceso
debe ser su unico escritor. En POSIX se crean directorio/archivos con modos
700/600; en Windows el operador debe restringir la ACL al usuario del servicio.
Todos los workers deben compartir la misma base y el mismo volumen durable.
No usar almacenamiento efimero de contenedor ni compartir el directorio por HTTP.

La implementacion inicial serializa escrituras: `BEGIN IMMEDIATE` en SQLite y
advisory lock transaccional en PostgreSQL. Cuota, clave idempotente y archivo quedan
protegidos entre procesos, a cambio de limitar el paralelismo de cargas. Esta
eleccion es explicita; la validacion local usa SQLite real. No se ha certificado
PostgreSQL, failover, rendimiento, volumen distribuido ni durabilidad ante corte
electrico en Windows. No se cambia infraestructura productiva.

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

## Errores

| HTTP | `code` |
|---|---|
| 400 | `solicitud_invalida`, `carga_interrumpida` |
| 401 | `sesion_invalida` (BFF), `no_autorizado` (servicio interno) |
| 403 | `alcance_insuficiente` |
| 404 | `siniestro_no_encontrado`, `poliza_no_encontrada`, `carga_no_encontrada`, `evidencia_no_encontrada` |
| 409 | `idempotencia_conflictiva`, `offset_conflictivo`, `carga_incompleta`, `carga_cancelada`, `carga_confirmada`, `limite_cantidad` |
| 411 | `longitud_requerida` |
| 413 | `limite_tamano` |
| 415 | `formato_no_permitido` |
| 422 | `integridad_invalida`, `formato_invalido`, `ubicacion_no_autorizada`, `video_fuera_limite`, `fecha_evento_invalida` |
| 503 | `servicio_no_disponible` |

Errores usan `application/problem+json` y `correlationId`. La auditoria SQL guarda
actor UUID, accion, resultado, fecha y correlacion; no cuerpos, archivos, claves,
tokens ni coordenadas. Rechazos sin acceso a SQL se registran en el log sin
parametros SQL ni datos del cuerpo.

## Ejecucion local

Instalar este proyecto junto con Identidad, Polizas y ambos BFF en un entorno
virtual propio. Configurar `SINIESTROS_DATABASE_URL`, `SINIESTROS_STORAGE_ROOT`,
`INTERNAL_API_TOKEN` y `POLIZAS_SERVICE_URL`. Luego:

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

Alcance de HU41/SOL-328: reanudacion y reconciliacion de operaciones de evidencia.
No implementa una cola universal de sincronizacion ni cierra SOL-328 completa.
