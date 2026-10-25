# Acceso con clave protegida por biometria

Esta fase permite acceder a **una cuenta existente** mediante una clave previamente
enrolada. Identidad comprueba la firma; Android protege el uso de la clave con
AndroidKeyStore, autenticacion por operacion y BiometricPrompt/CryptoObject fuerte.
Se conserva el registro, login con contrasena, RS256, refresh rotatorio y logout
existentes. No se transmiten ni almacenan plantillas de huellas o rostros.

**Sin attestation remota**, el servidor demuestra posesion de la clave registrada,
no su origen hardware ni que una huella fisica autorizo una operacion determinada.
No hay verificador JVM, certificados Android, roots ni booleano de cliente que
simule esa garantia. Rostro fuerte del OS no es selfie, identidad civil ni prueba
de vida. No se crea ni supone un estado KYC/onboarding validado. Esta entrega no
acredita esos criterios ni el cierre integral de HU17/SOL-314/SOL-169.

## Contratos y flujo

Las seis rutas del BFF estan en `contracts/openapi/bff-movil-v1.yaml`, con
componentes comunes en `biometria-v1.yaml`. Identidad valida nuevamente el bearer;
no confia en un usuario enviado por el BFF ni en campos del formulario.

| Metodo y ruta del BFF | Autorizacion | Resultado |
| --- | --- | --- |
| POST `/movil/v1/biometria/enrolamientos/desafio` | Bearer movil + contrasena actual | Desafio de enrolamiento, clave aun no activa |
| POST `/movil/v1/biometria/enrolamientos` | Bearer movil mismo titular + firma | 201, credencial activa sin emitir sesion |
| POST `/movil/v1/biometria/desafios` | credencialId opaco, sin bearer | Desafio de login |
| POST `/movil/v1/sesiones/biometria` | Firma, sin bearer/contrasena | Sesion movil con el formato existente |
| GET `/movil/v1/biometria/credenciales` | Bearer movil cliente | Solo credenciales propias |
| DELETE `/movil/v1/biometria/credenciales/{credencialId}` | Bearer movil cliente | 204; revoca clave/desafios/refresh asociados |

El alta recibe `{contrasena, clavePublica}`. La clave publica es SPKI DER P-256
canonico de 91 bytes, base64url sin padding (122 caracteres). Ni PEM ni certificado
ni clave privada. Cada alta requiere contrasena verificada de nuevo del `sub`
autoritativo; robar solo un bearer no basta para agregar una clave. No se guarda
contrasena para repetir el login.

Las dos confirmaciones reciben `{credencialId, desafioId, firma}`. El desafio devuelve
`{credencialId, desafioId, proposito, algoritmo, mensaje, expiraEn}`. Proposito:
`enrolar` o `login`; algoritmo fijo `ECDSA_P256_SHA256_DER`. La firma es ASN.1 DER
ECDSA P-256/SHA-256 (hasta 72 bytes), base64url sin padding, **no** JOSE ES256 raw
`r||s`. No se exige low-S: el replay se controla por desafio, no por bytes de firma.

`mensaje` contiene base64url de los bytes UTF-8 de estas nueve lineas, un solo LF
entre lineas, **sin LF final**, CR, BOM, JSON ni doble hash:

```text
SOLVENTA-BIOMETRIC-V1
<proposito>
movil
<b64u(SHA256(UTF8(JWT_ISSUER exacto configurado)))>
<desafioId>
<credencialId UUID minusculo con guiones>
<usuarioId UUID autoritativo>
<b64u(SHA256(SPKI DER canonico))>
<expiracion epoch UTC decimal entero>
```

No quitar `/`, espacios u otros bytes del issuer al construir el mensaje.
`Config` conserva sus validaciones existentes del emisor; el helper de firma no
normaliza el valor que recibe. Android debe decodificar `mensaje`, comprobar
contexto/cuenta/credencial/proposito/emisor esperado y firmar **esos bytes**
con `Signature.getInstance("SHA256withECDSA")` dentro de CryptoObject. No reconstruir
JSON ni firmar el texto base64 ni un digest calculado previamente.

Vector publico compartido Python/Java:
`services/identidad/tests/fixtures/biometric-v1.json`. No contiene clave privada.
Issuer sintetico exacto: `https://identidad.solventa.test`; es un identificador,
no afirma que exista un servidor/TLS en esa URL.

El BFF reenvia las rutas de administracion/desafio a `/v1/biometria/...` de Identidad,
incluido bearer original cuando aplica. El login se traduce a `/oauth/token` con
`grant_type=biometric_signature`, `client_id=movil` y los tres campos de prueba.
Este grant acepta solo JSON; no cambia los grants convencionales ni el JWE web.

## Vigencia, atomicidad y recuperacion

`desafioId` es el nonce aleatorio de 32 bytes, base64url de 43 caracteres. Expira
a los 60 segundos, sin gracia. Mensaje persistido vincula emisor/ambiente,
protocolo, proposito, canal, credencial, cuenta, hash de clave y expiracion.
El servidor ignora cualquier mensaje/usuario/clave adicional del cliente.

Cada confirmacion usa un CAS SQL de desafio vigente/no consumido. Antes de comparar
tiempo adquiere el bloqueo SQL y **consulta un reloj fresco despues de la espera**.
No usa una fecha capturada antes de bloquear ni `now()` transaccional PostgreSQL.
Solo una solicitud concurrente puede ganar. Login bloquea primero la credencial
para serializarse con revocacion, y confirma consumo, refresh y auditoria juntos.
No entrega tokens hasta commit. Fallo de auditoria revierte la sesion.

Si se pierde la respuesta de un login exitoso, repetir esa prueba responde 401:
solicitar otro desafio y una nueva autorizacion local. No repetir automaticamente
una firma. No se generan identidades nuevas ni se reactivan claves al recuperar.

Los refresh asociados conservan la FK de credencial al rotar. Revocar una credencial
bloquea nuevas pruebas y todos sus refresh, incluidas familias y descendientes.
Rotacion biometrica vuelve a comprobar vencimiento tras bloquear credencial/token.
Las familias convencionales no cambian. Reuso de refresh revoca su familia, como antes.
Access JWT emitidos mantienen su expiracion configurada (60 min por defecto,
con tolerancia existente del BFF); **no hay revocacion inmediata de access**.

Logout revoca la familia presentada pero conserva el vinculo biometrico.
Deshabilitar requiere DELETE y eliminar alias/metadata local. El DELETE es
idempotente y no revela si un ID pertenece a otra cuenta. Si el dispositivo
se pierde/invalida su clave, acceder convencionalmente, revocar la anterior y
enrolar explicitamente otra. Las credenciales no caducan automaticamente, igual
que las contrasenas actuales; desafios y sesiones si. Sin TTL de enrolamiento nuevo.

## Configuracion y operacion

`BIOMETRIC_LOGIN_ENABLED` acepta exclusivamente `true`/`false`, por defecto `false`
en Identidad y BFF movil. Con `true`, Identidad exige `REDIS_URL`; nunca usa memoria
como fallback de limites biometricos. Redis caido responde 503, no 401 ni exito.
Los endpoints apagados responden 503. Tener implementacion y flag opt-in no significa
que la funcionalidad este habilitada en produccion.

Las cuotas son compartidas entre replicas, por titular en alta y por credencial en
desafio/prueba, separadas por operacion. Reservan intentos atomicamente con Lua
INCR/EXPIRE/TTL y usan `AUTH_MAX_ATTEMPTS`/`AUTH_ATTEMPT_WINDOW_SECONDS`
(5/300 s por defecto). Incluyen intentos exitosos, no se reinician por nuevo nonce
ni por login valido, y un TTL=0 sigue denegando con Retry-After >=1.
La contrasena reutiliza ademas el limitador convencional.

JSON estricto, sin propiedades adicionales ni coercion, cuerpo maximo 8 KiB.
Errores problem+json/correlationId: 400 `solicitud_invalida`, 401
`sesion_invalida` o `credenciales_invalidas`, 403 `acceso_denegado`, 409
`credencial_ya_registrada`, 429 `demasiados_intentos` + Retry-After, 503
`servicio_no_disponible`. Firma erronea, replay, desconocido y vencido no se
distinguen en el mensaje de autenticacion. Respuestas `Cache-Control: no-store`.
El BFF exige el status upstream exacto: enrolar 201, desafios/listar/login 200
(incluido el grant `biometric_signature`), revocar 204 sin cuerpo. Otro 2xx,
JSON inesperado o respuesta vacia donde se exige JSON produce 503, nunca exito.

Ejecutar Alembic de Identidad `upgrade head` antes de habilitar. Migracion `0006`
agrega credenciales, desafios y auditoria, mas FK nullable de refresh. Preserva
usuarios, hashes y familias existentes. No necesita la clave JWT para migrar.
No hacer downgrade productivo sin revocar primero familias biometricas y
coordinar compatibilidad: al quitar la FK se pierde su condicion de vinculacion.

La auditoria durable guarda fecha, UUID internos cuando corresponde, correlacion,
operacion y resultado allowlist. No guarda cuerpos, passwords, correo, nonce,
firmas, claves ni tokens. Rechazos se confirman en transaccion separada del intento
fallido. Los desafios expirados se limpian en lotes de hasta 100 al crear nuevos;
la evidencia de auditoria y las claves revocadas no se borran.

HTTPS publico y proteccion de tokens siguen siendo obligatorios; conservar los
saltos productivos aprobados. **No se ha acreditado cifrado de transporte interno
productivo mediante las pruebas HTTP loopback**, ni se han cambiado redes/infra.
Los tokens siguen siendo Bearer: robar un access/refresh tiene los riesgos existentes;
esta firma no es DPoP ni elimina relay en tiempo real/dispositivo comprometido.

El codigo Terraform prepara exclusivamente POST `/movil/v1/biometria/desafios`
y POST `/movil/v1/sesiones/biometria` con autorizacion `NONE`, la integracion ALB
existente y los mismos limites de autenticacion del stage. Conserva el catch-all
movil con JWT: enrolar, listar y revocar permanecen protegidos. **No se ha aplicado
ni desplegado esta configuracion**; antes de habilitar en produccion hay que
coordinar su despliegue autorizado y comprobar ambas rutas publicas y la frontera
protegida. No se han cambiado permisos, recursos AWS ni rutas desplegadas.

## Evidencia local y alcance de pruebas

Pruebas unitarias/integracion cubren vector fijo, SPKI/DER/base64 canonicos,
enrolamiento no anonimo, titular/proposito/clave/canal diferentes, expiracion,
replay concurrente, expiracion DURANTE bloqueo SQL (alta/login/refresh),
cuota Lua concurrente y TTL=0, revocacion, rollback por auditoria y errores Redis.
Flujo HTTP real BFF/Identidad/SQL prueba las seis rutas, sesion JWT, rotacion,
logout conservando clave, revocacion y familia convencional independiente.

Los tests con `fakeredis[lua]` ejecutan Lua via Lupa pero son un **doble**, no
evidencia de Redis real/distribuido, failover, latencia o infraestructura. SQLite
ejercita concurrencia y bloqueo local; no acredita PostgreSQL productivo.
Los tests opt-in con `TEST_REDIS_URL` en base local/CI aislada `/15` comprueban
Redis real: cuota Lua concurrente, denegacion con TTL=0 y el flujo JWE existente.
Limpian solo sus claves unicas, nunca usan FLUSHDB/FLUSHALL. El stack de integracion
HTTP tambien se comprobo con Redis real 7.4.11 mediante tunel SSH loopback; no
acredita failover, replicas ni infraestructura productiva.
La huella fisica y propiedades Android requieren evidencia separada del dispositivo.
