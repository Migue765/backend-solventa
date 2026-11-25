# Cancelacion parametrica de vuelos

Candidato backend **3.3.6**, basado en `main`. No supone que esta revision este
desplegada. El flujo usa la compra normal; no se crean polizas ni pagos mediante SQL.
Open Data y desembolso son simulados. AWS, persistencia y transporte SES son reales
cuando se despliega/configura el sistema.

## Contrato de compra

El producto `parametric` mantiene `datosProducto`:
`airline`, `flightNumber`, `flightDate` y `coverageType`
(`delay-cancellation`, `missed-connection`, `comprehensive`).
La fecha debe ser futura al contratar. La vigencia sigue siendo exclusivamente el
dia UTC del vuelo; no se extiende a la fecha de compra.

Cada plan nuevo congela su indemnizacion en la instantanea de tarificacion y la
presenta en `coberturaPrincipal`. Sobre la base vigente del producto, Esencial,
Integral y Premium aplican los factores de plan ya usados por la tarificacion
(1, 1.15 y 1.30). La prima no es la indemnizacion. La emision toma la oferta
seleccionada y pagada, verifica ID/version/prima/moneda/periodicidad y conserva el
vuelo, la modalidad, la fecha de contratacion y el monto contratado en la poliza.

La cobertura de cancelacion se deriva de las coberturas incluidas en esa oferta:
Esencial de retraso y ofertas solo de conexion no indemnizan cancelacion.
Integral y Premium de retraso/cancelacion permiten comparar montos distintos.
Las polizas historicas sin contrato persistido **no** se vuelven elegibles
retroactivamente. Su emision previa sigue funcionando con aviso operativo.

## Evento autenticado

`POST /api/v1/backoffice/eventos-vuelo-simulados`, con sesion web vigente,
rol y alcance `asesor`. La pertenencia a socios se valida por el verificador JWT
existente; no se aceptan credenciales de un cliente para producir eventos.

```json
{
  "eventoId": "b8788b7b-8a8f-4092-a45f-53e7c04cab40",
  "ocurridoEn": "2026-12-15T12:00:00Z",
  "airline": "Aerolinea sintetica",
  "flightNumber": "ST123",
  "flightDate": "2026-12-15",
  "estado": "cancelado",
  "causa": "clima",
  "simulada": true
}
```

La fecha es ilustrativa, no debe enviarse como evento futuro. Se valida que el
evento ya haya ocurrido y pertenezca a la fecha UTC del vuelo.
`202` solo confirma recepcion en cola, **no** indemnizacion pagada.
La fuente interna es `opendata-vuelos-simulado`, con secreto propio; el BFF nunca
expone ese secreto al cliente.

El adaptador consulta contratos activos al servicio Polizas, exige aerolinea
normalizada + numero + fecha, cobertura de cancelacion por clima y compra/emision
estrictamente anteriores al evento. Registra reglas y evento en la misma
transaccion despues de comprobar conflictos y capacidad. Un `409` o `429` no
deja reglas nuevas. Los trabajadores existentes crean caso/siniestro y solicitan
el desembolso al proveedor simulado durable. Solo la confirmacion del proveedor
cambia el pago a confirmado.

Repetir el mismo ID/cuerpo no crea casos, pagos ni avisos. Repetir ID con cuerpo
distinto devuelve conflicto. Otro UUID para la misma cancelacion del mismo vuelo
y poliza conserva un unico caso/desembolso; los eventos recibidos conservan su
trazabilidad. La insercion del siniestro usa la identidad de negocio y conflicto
atomico tambien cuando dos trabajadores procesan eventos concurrentes.

## Consulta del cliente

Cada poliza de `GET /api/v1/clientes/me/dashboard` incluye:

```json
{
  "indemnizacion": {
    "estado": "pagada",
    "monto": "1725000.00",
    "moneda": "COP",
    "pagadaEn": "2026-12-15T12:01:00Z",
    "referencia": "b05050a1-d9f1-4ef9-a331-bf7f67817601",
    "simulada": true
  }
}
```

Sin caso, `indemnizacion` es `null`. Estados `pendiente` y `rechazada` tienen
`monto`, `pagadaEn` y `referencia` en `null`; nunca se presentan como pagados.
Vigencia/estado de poliza y prima permanecen separados.
La consulta de Siniestros filtra por cliente autenticado y el BFF valida el DTO.
Una dependencia no disponible no se convierte en una lista vacia exitosa.

## Correo y configuracion

Todos los envios SES pasan por un transporte comun de Identidad, incluida
recuperacion de contrasena. `EMAIL_REDIRECT_TO`, configurado explicitamente en
el secreto productivo `identidad-config`, reemplaza el destinatario fisico para
toda la aplicacion. No cambia el destinatario logico, tokens, vencimientos,
verificacion, outbox ni reintentos. Cada mensaje identifica usuario y
destinatario original. No registrar cuerpo, enlaces de recuperacion o secretos.

Notificaciones entrega email al endpoint **interno** autenticado de Identidad;
resuelve el correo desde la cuenta y no acepta un destinatario arbitrario.
La notificacion de indemnizacion de vuelo incluye monto/moneda/fecha/referencia
del pago confirmado, no una constante compartida. Los eventos climaticos
historicos conservan su payload para no romper fingerprints de reintentos.
`NOTIFICATION_EMAIL_DEFAULT_ENABLED=true`
habilita el canal por defecto; se respetan preferencias explicitas del usuario.
Errores SES/HTTP quedan como entrega fallida para los reintentos existentes.
Una referencia SES significa aceptacion por proveedor, no recepcion en buzon.

Configuracion necesaria al desplegar:

| Componente | Configuracion |
|---|---|
| BFF web | `SINIESTROS_SERVICE_URL`, `OPENDATA_VUELOS_SIMULADO_TOKEN` desde Secret |
| Siniestros API | `FUENTES_PARAMETRICAS_JSON` con `opendata-vuelos-simulado` y el mismo token |
| Workers Siniestros | Imagen actualizada, `POLIZAS_SERVICE_URL`, token interno y BD existentes |
| Identidad | Configuracion SES existente, `EMAIL_REDIRECT_TO` secreto y permisos SES ya autorizados |
| Notificaciones | `IDENTIDAD_URL`, token interno compartido y canal email habilitado |
| Polizas | Migracion aditiva `polizas_0007`; no reconstruye contratos antiguos |

No se requiere otro proveedor de pago, endpoint de pago al cliente, CodeBuild,
CodePipeline, infraestructura nueva ni permisos IAM globales.

## Aceptacion y despliegue pendientes

Antes de publicar/desplegar, reconciliar la fuente autorizada:
el `main` verificado `bb53460` no contiene la fuente productiva `918d743`
(imagen 3.3.5). Desplegar simplemente desde este `main` perderia cambios
productivos de PDF/historial. No resolverlo copiando silenciosamente otros
worktrees. El despliegue queda bloqueado hasta una decision explicita.

El destino contrastado es cuenta `096571798698`, `us-east-2`,
EKS `solventa-prod-eks`, API `r92im9tgi6`. Usar kubeconfig exclusivo y contexto
explicito: el kubeconfig global de este computador apuntaba a otra cuenta.
Conservar imagenes/digests previos, aplicar migracion compatible, construir
imagenes localmente, publicar ECR con version/digest inmutable y desplegar
directamente sin reactivar pipelines. Un rollout Ready no acredita el flujo.

La aceptacion inmediata tambien requiere esperar la fecha real del vuelo:
no adelantar reloj, evento, emision ni datos productivos para forzar vigencia.
Las pruebas con reloj controlado pertenecen exclusivamente a tests locales.

La coleccion Postman entregada como artefacto de sesion contiene registro/login
cifrados, dos compras normales, emision por pago, evento autenticado, replay y
consulta/ownership. Importar coleccion y entorno en Postman Desktop reciente,
autorizar Vault y el paquete cifrador `node-forge@1.3.1`. Configurar correos
autorizados y documentos sinteticos libres en valores locales. Guardar la
contrasena exclusivamente en Vault como `solventa-password`; scripts guardan
tokens/estado tambien en Vault, no en valores compartidos ni exportaciones.
Cada cliente consulta y autoriza explicitamente el consentimiento de
perfilamiento antes de cotizar; aceptar terminos de registro no lo sustituye.
Las claves de idempotencia quedan en Vault por actor y operacion y se conservan
en cada reintento. Solo para otra compra deliberada, eliminar manualmente las
entradas `solventa-cliente1-state` y `solventa-cliente2-state`; nunca reiniciarlas
ante un resultado de pago ambiguo.
No permite resetear cuentas existentes ni elevar roles; el asesor debe estar
provisionado por el mecanismo autorizado existente.

Ejecutar carpeta de compras antes del vuelo. Fijar `eventoId` una sola vez y
`ocurridoEn` real despues de ambas compras, ya dentro de vigencia. Ejecutar carpeta
de evento; dar tiempo acotado a workers antes de consultar. Si sigue pendiente,
registrar ese resultado y repetir solo consultas, no comprar otra vez.
Conservar referencia de cada pago antes/despues del replay y comparar montos
contratados diferentes. Completar tambien controles de otro vuelo/no elegibles
y comprobacion de correo recibido; no basta con aceptacion SES.

No se han creado usuarios, compras o pagos productivos para esta revision, no
se ha cambiado AWS y no se ha acreditado recepcion de correo ni ejecucion en
Postman Desktop. Los JSON se validan estructuralmente, no sustituyen esa
ejecucion pendiente.
