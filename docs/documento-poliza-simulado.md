# Documento de poliza y aceptacion simulados

Este flujo es **academico y sin validez legal**. No constituye firma electronica,
digital ni certificada; no cambia emision, cobertura o pagos. OpenFinance/OpenData
y pagos siguen simulados. No requiere proveedor, certificado, AWS ni libreria nueva.

## Contrato WEB

Contrato completo: `contracts/openapi/documento-poliza-web-v1.yaml`, referenciado
desde el contrato BFF Web. El JWT debe tener rol y scope `cliente`. El actor sale
del usuario autenticado; no se permite seleccionar otro cliente mediante headers,
parametros o cuerpo. Recurso inexistente y ajeno responden igual: 404.

1. `GET /v1/clientes/me/polizas/{polizaId}/documento` devuelve el contenido
   estructurado, `documentoId`, `versionPoliza`, `contenidoHash`, `formato: html`,
   `simulada: true`, aviso, `aceptacionActual` e `historialAceptaciones`.
2. `GET /v1/clientes/me/polizas/{polizaId}/documentos/{documentoId}/descarga`
   descarga un HTML legible autocontenido, tanto vigente como historico.
3. `POST /v1/clientes/me/polizas/{polizaId}/aceptaciones` exige `Idempotency-Key`
   UUID y cuerpo `{documentoId, versionPoliza, contenidoHash, aceptaSimulacion: true}`.
   Devuelve 201 con evidencia nueva o 200 con la evidencia original del replay.

La evidencia contiene los metadatos del documento mas `id`, `actorId` y
`aceptadaEn` UTC del servidor. No admite actor/fecha suministrados por el cliente.
Repetir la misma clave y cuerpo no duplica evidencia ni auditoria; cambiar el
cuerpo/poliza con esa clave produce `409 idempotencia_conflictiva`.

Una confirmacion nueva de documento/version/hash obsoletos responde
`409 documento_desactualizado`: consultar de nuevo y requerir otra accion explicita
con nueva clave. Si se perdio una respuesta, repetir exactamente clave y cuerpo;
un replay historico NO acepta el documento vigente. Un 503 nunca significa exito.

Los demas errores son 400 `solicitud_invalida`, 401 de sesion, 403
`alcance_insuficiente`, 404 `poliza_no_encontrada` y 503
`servicio_no_disponible` con `Retry-After: 5`. El BFF usa Problem Details
(`application/problem+json`); Polizas internamente usa `{error, correlationId}`.

## Contenido, formato y privacidad

La fuente es la poliza persistida, su vigencia/estado, suscripcion, regla de emision,
version de condiciones y catalogo academico ya usado por consultas/modificaciones.
En nuevas emisiones mensuales del modelo academico, Cotizacion transmite
`condicionesEmision` desde el plan aceptado de su instantanea: plan, prima pagada
y nombres de coberturas. Polizas valida las coberturas contra el producto y guarda
la version 1 en la misma transaccion de emision. Documento, consultas, renovacion
y liquidacion usan esa prima persistida, sin volver a calcular el riesgo ni
sustituirla por la tarifa fija de endosos. Los cambios de plan siguen requiriendo
la aceptacion explicita de su impacto y registran una nueva version.

Los BFF Web y movil conservan por separado los indicadores `opcional` e `incluida`
del catalogo de modificaciones. `opcional` indica si una cobertura puede elegirse
como adicional en un endoso; no exige que este incluida en todas las emisiones.
La inclusion vigente e historica procede de la version persistida del plan
aceptado, sin agregar coberturas ni rechazar el documento de un plan Esencial.

La compatibilidad con emisiones anteriores sin esas condiciones no reconstruye
la oferta original: conserva el modelo anterior. No se modifican versiones,
documentos ni aceptaciones historicas; su conciliacion requiere evidencia original
y una operacion separada, no se acredita con esta correccion.

Los productos por fechas
conservan `versionPoliza: null`, `plan: null` y `prima: null`, con sus coberturas
del modelo. Las primas de productos con plan son las del modelo academico existente,
no una nueva valoracion financiera ni el precio real de una aseguradora.

`contenidoHash` es SHA-256 de **los bytes HTML UTF-8 exactos descargables**, no
del objeto JSON `contenido`. El HTML se genera en servidor con Jinja2 y autoescape,
sin scripts, formularios ni URLs externas. Se persisten texto y HTML originales.
`documentoId` permanece estable para el mismo HTML/poliza. No hay `consultadaEn`
ni otro reloj volatil en el contenido. Fecha/actor de aceptacion se conservan
por separado para no modificar los bytes aceptados.

Descarga: `text/html; charset=utf-8`, `Content-Disposition: attachment` con
nombre UUID seguro terminado en `-simulada.html`, `X-Content-SHA256`,
`Cache-Control: no-store`, `X-Content-Type-Options: nosniff`, CSP
`default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'; sandbox`.
El consumidor puede verificar SHA-256 de `arrayBuffer()` contra el hash del GET.
No insertar el HTML descargado como contenido activo del DOM. El navegador no
necesita leer Content-Disposition para construir un nombre seguro propio.
No se cambia CORS: en despliegues cross-origin los headers personalizados pueden
no estar expuestos; el hash del GET sigue disponible para verificar bytes.

## Historial, lifecycle y persistencia

`documentos_poliza` y `aceptaciones_documento` son append-only desde el servicio.
Se reutilizan transacciones, unicidad idempotente por cliente y auditoria existente.
La migracion `polizas_0005` agrega ambas tablas; downgrade las elimina sin cambiar
polizas/versiones/cancelaciones existentes. Hacer backup antes de un downgrade
porque elimina estas evidencias academicas.

Modificar condiciones cambia version y hash; renovar cambia vigencia/hash aunque
la version de condiciones siga igual; cancelar cambia estado/hash. Las aceptaciones
anteriores quedan en el historial y nunca se copian al nuevo documento.
La aceptacion es una constancia de lectura simulada del estado mostrado, incluso
si la poliza esta cancelada o vencida: NO reactiva cobertura ni bloquea lifecycle.
Las descargas historicas verifican ownership actual de la poliza en cada acceso.
Los snapshots ya persistidos se descargan sin rerenderizarlos, incluso tras cambiar
el catalogo o la plantilla en una futura version.

## Validacion sintetica

`python -m pytest tests/test_policy_document_flow.py` ejecuta BFF y Polizas contra
SQLite migrada y HTTP loopback con JWT RSA sintetico. No necesita cuentas reales,
servicios externos, OpenFinance ni pagos reales. Tambien se conservan las pruebas
de consultas, modificaciones, renovaciones y cancelaciones del servicio.
