# Demostracion academica de identidad

Este flujo **no es KYC ni prueba de vida acreditada**. No verifica documentos, selfies,
rostros ni personas reales. No modifica permisos, registro, login, acceso con huella,
credenciales biometricas ni consentimiento. Su resultado siempre incluye `simulada: true`.
Solo esta disponible despues del login de una cuenta cliente movil con sesion activa.
No sustituye la revision pre-registro de fotos de Android.

## Activacion y escenarios sinteticos

Aplicar migraciones de Identidad (`alembic upgrade head` en su directorio, con la conexion
del entorno configurada). Las tablas propias son `verificaciones_identidad_simuladas` e
`intentos_identidad_simulados`; no se crean tablas al atender HTTP.

Establecer `IDENTIDAD_SIMULADA_ENABLED=true` **en Identidad y BFF movil** en el entorno
academico. Por defecto es `false` en ambos: se responde 503 y nunca se simula exito
localmente si el backend no esta disponible.

Solo en Identidad, `IDENTIDAD_SIMULADA_RESULTADOS` acepta de uno a tres valores separados
por coma: `aprobado`, `documento_no_valido`, `prueba_vida_no_valida`. Ejemplos:

- `aprobado` (por defecto): aprueba el primer intento.
- `documento_no_valido,aprobado`: rechazo recuperable, luego aprobacion.
- `prueba_vida_no_valida`: tres rechazos; el ultimo resultado configurado se repite.

La secuencia se congela al crear la sesion, no la decide el cliente ni cambia al reiniciar
el servicio. No hay esperas, temporizadores, worker ni proveedores externos.

## Contrato movil

El contrato completo esta en `contracts/openapi/identidad-simulada-v1.yaml`; Identidad
expone las mismas rutas sin `/movil`. Todas las respuestas llevan `Cache-Control: no-store`
y `X-Correlation-ID`. Los errores usan `application/problem+json`.

1. `POST /movil/v1/verificaciones-identidad` con `{}` crea o recupera la unica sesion por
   cliente (200). Estado inicial `pendiente`, `intentos: 0`.
2. `POST /movil/v1/verificaciones-identidad/{verificacionId}/intentos` procesa un intento
   con `{ "intentoId": "<UUID nuevo>", "documentoRef": "fixture:documento-v1",
   "selfieRef": "fixture:selfie-v1" }` (200).
3. `GET /movil/v1/verificaciones-identidad/{verificacionId}` consulta el estado actual.

Respuesta: `verificacionId`, `estado` (`pendiente`, `aprobado`, `rechazado`), `intentos`,
`maxIntentos: 3`, `reintentoPermitido`, `simulada: true` y `motivo` (`null`,
`documento_no_valido` o `prueba_vida_no_valida`). Un rechazo del proveedor es un resultado
200, no una falla de transporte. Solo un rechazo con menos de tres intentos permite
reintentar con UUID nuevo. Aprobacion o tres rechazos son terminales (nuevo intento: 409).

Repetir exactamente un intento devuelve su snapshot historico, incluso tras aprobar mas
tarde; no suma intentos. Reutilizar su UUID con otras referencias validas da
`409 conflicto_idempotencia`. Para conocer el estado vigente tras un replay usar GET.
El inicio es idempotente por cliente: repetirlo tras agotar intentos, cerrar sesion o
reiniciar no reinicia el cupo. **No hay reset publico ni recuperacion automatica**:
otra demostracion requiere una cuenta sintetica distinta, preparada para las pruebas.
El limite y la idempotencia se serializan en persistencia por propietario.

401 indica token movil ausente/invalido/vencido o sesion revocada/vencida; 403 rol/alcance
distinto de cliente; 404 no distingue verificacion inexistente de ajena; 400 indica
payload/query invalido; 409 transicion terminal o conflicto de idempotencia; 503 demo
deshabilitada o dependencia no disponible. El consumidor no debe convertir estos errores
en aprobacion ni cambiar al flujo local de temporizador.

## Limites de privacidad

Solo se aceptan referencias de la lista cerrada `fixture:documento-v1|v2` y
`fixture:selfie-v1|v2`. Son etiquetas sinteticas, no archivos ni tokens de carga; ambas
versiones permiten demostrar conflictos de idempotencia y **no influyen en el resultado**.
No representan las fotos capturadas por Android. El backend rechaza imagenes, URLs,
base64, numeros de documento, propietarios/resultados impuestos y campos extra.
El cuerpo JSON tiene un maximo de 2048 bytes.

No enviar fotos reales ni PII a este flujo, a registros ni a terceros. Solo se conserva
el identificador interno del usuario ya autenticado, UUIDs de sesion/intento, etiquetas
sinteticas, secuencia mock y resultados. Android debe mostrar el caracter academico,
mantener cualquier captura fuera de estas peticiones y descartar estado de pantalla al
cerrar sesion/cambiar de cuenta. Verificar identidad real requeriria otro diseno,
autorizacion de privacidad y proveedor; esta implementacion no lo habilita.
