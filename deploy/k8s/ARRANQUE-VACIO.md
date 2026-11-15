# Arranque vacio y reconexion publica

Preparacion para `396383602233/us-east-2`. Este procedimiento requiere planes,
revision y autorizacion de ejecucion separados. Hoy no crea recursos, cuentas,
secretos ni datos; tampoco publica imagenes o frontend.

Conservar Jenkins y origen sin cambios. Usar VPC, roles, buckets y states nuevos
del backend. No importar recursos, migrar state, restaurar snapshots ni copiar
fixtures locales. No ejecutar `scripts/migrate_perfilamiento.py stage/retire`:
ese procedimiento historico presupone HU11 y la cuenta anterior.

## 1. Gates antes de ejecutar

- Confirmar plan/eligibilidad AWS y presupuesto; mas creditos no acreditan PAID
  ni permiso para crear EKS. Usar perfil de rol asumido propio, no root ni Jenkins.
- Aprobar SHA/imagenes inmutables de las nueve APIs, publicacion necesaria y
  planes de bootstrap, network, primary y addons. Hoy el diff no esta publicado.
- Aprobar CIDRs y roles administradores EKS reales. Comprobar STS de provider
  y backend antes de cada init/plan/apply; preservar el baseline de Jenkins.
- Preparar DSN/usuarios de aplicacion, acceso privado a RDS/Redis y entrega
  protegida de Secrets. No inventar ARNs, contrasenas, certificados o endpoint.
- Conservar restricciones de trafico del corte hasta verificar JWE, roles y
  recorrido autenticado. No anunciar disponibilidad por un `/ping`.
  La compuerta de login recomendada abajo necesita aprobacion antes del primer
  arranque; hoy no se ha aplicado ni validado en AWS.
  El helper JWE exige una replica activa; no puede ejecutarse sobre la semilla cero.

Seguir primero [infraestructura](../../terraform/environments/prod/primary/README.md#preparacion-y-primer-despliegue):
bootstrap, network y primary con states separados nuevos; despues addons.
Exportar solo el `network_context` revisado de network a primary; no remote_state,
import ni movimientos entre roots. La creacion de un pipeline
puede iniciar Source/Build; mantener aprobacion de Deploy y ejecuciones secuenciales.
No arrancar nueve rollouts/Jobs juntos en los tres nodos.

## 2. Bases y Secrets nuevos

El RDS declarado no crea todas las bases, usuarios o tablas de aplicacion.
Un operador autorizado debe preparar aislamiento por dominio en esa instancia,
sin usar el master de RDS como usuario runtime. Identidad usa `DB_SCHEMA=identidad`;
su Job crea ese esquema si falta y necesita permiso dentro de su base.
No ejecutar DDL contra una base cuya pertenencia al destino no se haya comprobado.

Las siete imagenes SQL instalan `.[postgres,server]` con psycopg3. Los DSN deben
usar **`postgresql+psycopg://`**, con usuario/password codificados para URL y
parametros TLS/CA revisados para RDS. `postgresql://` selecciona psycopg2, que esas
imagenes no instalan. No elegir SQLite, desactivar TLS ni omitir la comprobacion
de conectividad PostgreSQL para hacer pasar el arranque.

| Namespace | Secret y claves requeridas |
|---|---|
| cotizacion | `cotizacion-db/DATABASE_URL`; `profiling-internal/INTERNAL_API_TOKEN` |
| identidad | `identidad-config/{DATABASE_URL,REDIS_URL,JWT_PRIVATE_KEY}`; `profiling-internal/INTERNAL_API_TOKEN`; Secret JWE dedicado |
| perfilamiento | `perfilamiento-runtime/{PERFILAMIENTO_DATABASE_URL,INTERNAL_API_TOKEN}` |
| polizas | `polizas-runtime/{POLIZAS_DATABASE_URL,INTERNAL_API_TOKEN}` |
| siniestros | `siniestros-runtime/{SINIESTROS_DATABASE_URL,INTERNAL_API_TOKEN}` |
| notificaciones | `notificaciones-runtime/{NOTIFICACIONES_DATABASE_URL,INTERNAL_API_TOKEN}` |
| asistencias | `asistencias-runtime/{ASISTENCIAS_DATABASE_URL,INTERNAL_API_TOKEN}` |
| bff-web / bff-movil | `profiling-internal/INTERNAL_API_TOKEN` en cada namespace; sin DB |

Generar un token interno nuevo y distribuir los mismos bytes a esos Secrets.
No agregar salto de linea: algunos consumidores recortan y otros comparan el
token exacto. No traer el token, claves ni passwords del origen o stack local.
JWT usa la clave nueva del destino; JWE requiere otra RSA privada persistente,
independiente de JWT, segun [operacion del login](../../docs/operacion-login-cifrado.md).
Redis debe ser real, con URL `rediss://` y operaciones `SET NX EX` / `GETDEL`;
no usar fakeredis ni un limitador en memoria en la entrega AWS.

Entregar valores por archivos nuevos fuera de Git, con acceso restringido.
Usar `kubectl create secret generic --from-file=CLAVE=ARCHIVO` y pipe de
`--dry-run=client -o yaml` a `kubectl apply`, con contexto destino explicito.
No pasar passwords/DSN/PEM por `--from-literal`, argumentos, chat o logs.
No usar `set -x`, `tee` ni imprimir el YAML de Secrets. Esta receta no instala
External Secrets/CSI ni concede acceso a Secrets Manager.

Los nombres de archivo y valores reales se acuerdan por canal protegido antes
de ejecutar. Verificar por nombres de claves, sin `kubectl get secret -o yaml`.
Si rota RDS/JWT/Redis, actualizar su Secret y comprobar consumidores; una copia
manual no implementa sincronizacion automatica.

## 3. Migrar antes de arrancar pods

Crear namespaces, Secrets y ServiceAccounts con roles IRSA del destino antes de
los Jobs. Siniestros necesita ademas su bucket real, CMK y rol IRSA; su `emptyDir`
solo sirve para cargas en curso, no para evidencia confirmada.

| Servicio | Comando del Job versionado |
|---|---|
| cotizacion | `alembic upgrade head` (`deploy/k8s/migrate-job.yaml`) |
| identidad | `alembic upgrade head`, `DB_SCHEMA=identidad` |
| perfilamiento | `python -m flask --app perfilamiento.app init-db` |
| polizas / siniestros / notificaciones / asistencias | `alembic upgrade head` en su imagen/namespace |
| bff-web / bff-movil | Sin Job; `MIGRATE=false` |

Esperar cada Job completado y comprobar su revision/tablas con el operador, sin
leer filas de clientes. Perfilamiento usa `create_all`, no Alembic. Heads de esta
revision: Cotizacion `pagos_simulados_pendientes`, Identidad `0008`, Polizas
`polizas_0001`, Siniestros `0004_eventos_parametricos`, Notificaciones
`0001_notificaciones` y Asistencias `0001_asistencias`.
Las migraciones siembran reglas academicas y tres prestadores sinteticos de
Asistencias; no equivalen a recuperar clientes o polizas del origen.

`deploy.sh` necesita Deployment existente para Cotizacion, Identidad, Perfilamiento
y los BFF. Preparar su Service y Deployment semilla con **replicas cero** y sin
HPA hasta migrar. En Bash del host de despliegue, con variables reales exportadas
y revisadas:

```bash
set -euo pipefail
# K8S_DIR es deploy/k8s para Cotizacion; deploy/k8s/<servicio> para los demas.
# CONTEXT, IMAGE y las variables ${...} del manifiesto deben estar completas.
: "${CONTEXT:?}" "${K8S_DIR:?}" "${IMAGE:?}"
export IMAGE
envsubst < "$K8S_DIR/deployment.yaml" |
  kubectl patch --local -f - --type=merge -p '{"spec":{"replicas":0}}' -o yaml |
  kubectl --context "$CONTEXT" apply -f -
```

Revisar el renderizado no secreto antes de aplicar; `envsubst` no valida
variables ausentes. Identidad necesita `LOGIN_ENCRYPTION_SECRET_NAME` incluso
con el flag apagado: una referencia opcional no admite nombre vacio.
Usar imagenes aprobadas del ECR nuevo; issuer/audience vienen de outputs del
destino. El pipeline migra y despues escala/aplica HPA y actualiza la imagen.
Polizas/Siniestros/Notificaciones/Asistencias admiten su primer Deployment sin
semilla. No aplicar un Deployment completo sobre un runtime existente para
reintentar: podria borrar flags o referencias a Secrets.

### Compuerta recomendada: diferir los TargetGroupBindings de ambos BFF

Para esta reconstruccion vacia, **no aplicar** los manifiestos
`bff-web/targetgroupbinding.yaml` y `bff-movil/targetgroupbinding.yaml` hasta el
final de la activacion privada. Los pods pueden tener una replica y recibir
`kubectl exec`/trafico ClusterIP sin estar registrados en el ALB. El helper usa
localhost dentro del pod y el DNS interno de Identidad, no necesita esos bindings.
Identidad puede mantener su binding de discovery/JWKS.

Base local de la recomendacion: `terraform/modules/api-gateway` envia
`/v1/sesiones*` al TG Web (prioridad10) y `/movil/*` al TG Movil (prioridad20);
discovery va a Identidad (prioridad30). Los Services BFF son ClusterIP,
`deploy.sh` no aplica sus TargetGroupBindings y la politica del controller contiene
RegisterTargets/DeregisterTargets. Conservar esas reglas: no retirar la regla
del listener, pues se podria caer en el forward por defecto a Cotizacion.
No requiere WAF/resource policies de HTTP API, modificar JWT/CORS ni agregar
infraestructura. Ocultar la URL, OPTIONS y `/ping` no son compuertas.

Es una **recomendacion derivada del checkout**, no una compuerta probada en AWS.
Su efecto es cerrar todos los endpoints de ambos BFF, no solo login. No usarla
como mantenimiento sin autorizar esa indisponibilidad. No cambia Jenkins ni el
origen. No aplicar Kubernetes recursivamente ni permitir que otra automatizacion
recree los bindings mientras esta cerrada.

**Cierre y comprobacion antes de cambiar flags.** En primer arranque basta
omitir ambos bindings, pero comprobar tambien que los TG nuevos estan vacios.
Si ya existian, con autorizacion quitarlos y esperar la limpieza del controller:

```bash
set -euo pipefail
: "${CONTEXT:?}" "${AWS_PROFILE:?}" "${BFF_WEB_TARGET_GROUP_ARN:?}" "${BFF_MOVIL_TARGET_GROUP_ARN:?}"
kubectl --context "$CONTEXT" -n bff-web delete targetgroupbinding bff-web \
  --ignore-not-found --wait=true --timeout=120s
kubectl --context "$CONTEXT" -n bff-movil delete targetgroupbinding bff-movil \
  --ignore-not-found --wait=true --timeout=120s
for group in "$BFF_WEB_TARGET_GROUP_ARN" "$BFF_MOVIL_TARGET_GROUP_ARN"; do
  count="$(aws elbv2 describe-target-health --profile "$AWS_PROFILE" --region us-east-2 \
    --target-group-arn "$group" --query 'length(TargetHealthDescriptions)' --output text)"
  if [ "$count" != "0" ]; then
    echo "Compuerta NO confirmada: quedan targets registrados o drenando." >&2
    exit 1
  fi
done
```

Los ARNs salen de `bff_web_target_group_arn` y `bff_movil_target_group_arn` del
primary destino, tras verificar STS/contexto/pertenencia. No forzar finalizers,
editar targets a mano ni continuar si hay otro binding/Ingress/registro que
controle esos TG. El delay declarado es30s, pero esperar30s no prueba limpieza.
**Targets unhealthy no significa cierre:** ALB puede enviarles trafico; exigir
cero targets registrados, sin draining.

Desde fuera del cluster, comprobar repetidamente `GET /v1/sesiones/desafio`,
`POST /v1/sesiones` y `POST /movil/v1/sesiones` (los POST con `{}` y
Content-Type JSON, sin credenciales). Se espera indisponibilidad503 por TG vacio;
un400/401/422 de aplicacion o cualquier2xx no confirma cierre. Si aparece otro
error Gateway, investigar su causa: un fallo de conectividad no valida por si solo
la compuerta. Conservar tambien evidencia de TG vacios y de los bindings ausentes.

**Activacion privada.** Mantener el cierre durante `prepare` y `enable` de
Identidad y despues BFF Web, segun el runbook JWE. En primer arranque,
`--expected-image` de prepare es la imagen actual aprobada, aunque sea la misma
que `--image`; no inventar una version anterior. Ejecutar `verify` de ambos con
contexto/imagen exactos y comprobar flag `ENCRYPTED_LOGIN_ENABLED=true` en ambos
contenedores. El verify de Identidad prueba criptografia/Redis, no sustituye esa
comprobacion del flag. Comprobar el flujo Movil por acceso privado antes de abrir
su TG, sin activar biometria ni cambiar su contrato. No crear un tunel publico.

**Apertura autorizada.** Solo despues de esos checks, aplicar cada binding
existente con el ARN de su propio output (nunca intercambiar ambos):

```bash
set -euo pipefail
: "${CONTEXT:?}" "${BFF_WEB_TARGET_GROUP_ARN:?}" "${BFF_MOVIL_TARGET_GROUP_ARN:?}"
export TARGET_GROUP_ARN="$BFF_WEB_TARGET_GROUP_ARN"
envsubst '${TARGET_GROUP_ARN}' < deploy/k8s/bff-web/targetgroupbinding.yaml |
  kubectl --context "$CONTEXT" apply -f -
export TARGET_GROUP_ARN="$BFF_MOVIL_TARGET_GROUP_ARN"
envsubst '${TARGET_GROUP_ARN}' < deploy/k8s/bff-movil/targetgroupbinding.yaml |
  kubectl --context "$CONTEXT" apply -f -
```

Esperar targets healthy y comprobar entonces por Gateway desafio200/no-store,
login cifrado sintetico, rechazo de plaintext/replay, errores/roles y flujo Movil.
Publicar el frontend solo tras esa aceptacion. Si falla, volver a cerrar mediante
los bindings, confirmar cero targets e indisponibilidad antes de revertir flags
o imagenes. No aplicar Terraform para un rollback operativo de esta compuerta.

## 4. Orden y activaciones

1. Migrar todos los dominios. Arrancar Perfilamiento, Polizas y Notificaciones.
   Perfilamiento puede arrancar antes de Identidad, pero su `/health` no acredita
   llamadas a Identidad; no habilitar consumidores publicos todavia.
2. Arrancar Identidad cuando el consumidor de consentimiento de Perfilamiento
   este    preparado. Su pipeline aplica el Outbox CronJob despues del rollout.
   Comprobar Redis, DB, discovery y JWKS, sin imprimir JWT ni claves.
3. Arrancar Cotizacion y activar `POLIZAS_SERVICE_URL` solo con Polizas
   verificado, y `NOTIFICACIONES_SERVICE_URL` con Notificaciones verificado.
   Usar los DNS versionados `http://<servicio>.<servicio>.svc.cluster.local:8080`.
   Conservar `COTIZACION_PERFILAMIENTO_HABILITADO=true` y su URL de Perfilamiento.
   `COTIZACION_CONCILIACION_EMISION_HABILITADA` permanece opt-in: para probar
   recuperacion automatica de emision academica, aprobar su activacion despues
   de migrar/verificar Polizas; no hace falta para recuperar pagos pendientes.
4. Arrancar Siniestros con Polizas; despues Asistencias con ambos.
   CodeBuild recibe `SINIESTROS_EVIDENCIAS_BUCKET` desde Terraform. Para un
   render manual usar el output `siniestros_evidence_bucket`, no un nombre antiguo.
5. Arrancar BFF Web (Identidad/Cotizacion/Perfilamiento/Siniestros) y BFF Movil
   (Identidad/Siniestros/Notificaciones/Asistencias). Sus manifiestos ya contienen
   esas URL; no agregar a Movil contratos de cotizacion/polizas que no implementa.
6. Con ambos TargetGroupBindings BFF todavia retenidos, preparar/habilitar/verificar
   JWE con el helper existente, primero Identidad y
   despues BFF Web, usando contexto e imagenes explicitos. Conservar biometria
   apagada hasta una activacion coordinada con Android. No cambiar flags por
   `set image` ni declarar que el login esta listo antes de verificar el gateway.
7. Crear nuevas cuentas sinteticas para aceptacion. Cliente: registro vigente
   `GET /v1/usuarios/desafio` y `POST /v1/usuarios` cifrado desde el consumidor
   compatible, con datos maestros y eleccion explicita de consentimiento.
   Verificar true/false persistidos con usuario/outbox en una transaccion,
   TTL60/replay400 y respuesta201 sin tokens. Requiere ambos flags activos y
   la nueva ruta publica revisada; el plan881 anterior no contiene esa ruta.
   Socio: CLI existente `python -m flask --app identidad.app create-partner-user`
   con nombre/correo/socio sinteticos nuevos y password por prompt oculto;
   no usar la opcion `--password` ni conceder roles desde el registro publico.
   No reutilizar las cuentas/passwords/poliza de la aceptacion local.

## 5. Readiness y aceptacion

`/ping` de Identidad/BFF solo comprueba proceso; Cotizacion tambien detecta un
hilo conciliador muerto. `/health` de Perfilamiento/Polizas/Siniestros/Asistencias
no consulta DB. Notificaciones si ejecuta `SELECT 1`; tampoco prueba todo el flujo.
El `/ping` publico se integra al ALB y su forward por defecto a Cotizacion:
un200 no prueba los BFF, JWE ni las bases. Puede responder con la compuerta BFF cerrada.

Antes del cutover comprobar, en destino y con datos sinteticos autorizados:

- Jobs/migraciones, `SELECT 1` por dominio y acceso a sus tablas con el usuario
  runtime; endpoints de Services y targets ALB saludables. No exportar datos.
- Discovery/JWKS publicos con issuer exactamente igual a `jwt_issuer`; claves
  nuevas y gateway verificando tokens nuevos. Los tokens del origen no se migran.
- Desafio JWE y login por `/v1`, credencial incorrecta401, reuso400 y autorizacion
  de cliente/socio; usuario cliente denegado en portal de socio. Refresh web y
  `/movil/v1` por el contrato vigente, sin habilitar biometria para el smoke.
- Consentimiento completo, perfilamiento, cotizacion, ofertas, seleccion y pago
  **simulado**, con idempotencia y propietario correcto. Una poliza emitida por
  ese recorrido es academica. Otro cliente no debe verla ni acceder a sus claims.
- Claims/evidencias sinteticas solo en la aceptacion autorizada: confirmar en S3,
  leer despues de reiniciar un pod y verificar integridad/propiedad. No confundir
  staging `emptyDir` ni un doble S3 con evidencia durable en AWS.
- Notificaciones `in_app`, preferencias y Asistencias con prestadores sinteticos.
  `email`/`push` siguen sin proveedor. Comprobar Outbox y despachos CronJobs sin
  imprimir tokens ni crear un reporte de siniestro en nombre de la usuaria.

Conservar `FuenteMock`, pagos/ofertas/polizas academicos y prestadores sinteticos
identificados como tales. Esta reconstruccion restaura la aplicacion publica
academica; no integra banca/aseguradora/pasarela/firma real ni acredita HA/SLA.

## 6. Cutover web y movil

La configuracion web inspeccionada en `Solventa Web PF2`, SHA
`686f6236e258f2539fd03e57373e4d52d51fffba`, usa `dataAccessMode=backend`.
`environment.ts` y `environment.backend.ts` contienen
`https://65qgzhody9.execute-api.us-east-2.amazonaws.com`. CI compila
`pnpm build:pages` en production; Pages publica ese artefacto validado desde
`master`, no recompila ni consulta Terraform en runtime.
Web Lead confirmo tambien esos dos valores en el bundle publico de
`https://jazmincorandes.github.io/solventa-web-pf2/`; es evidencia del destino
configurado, no de login o API funcionales.

Despliegue debe entregar al coordinador URL HTTPS/base/stage reales desde
`api_endpoint`, `jwt_issuer` y `movil_api_url`. El stage declarado es `$default`;
no inventar `/prod` ni conservar el hostname de otra cuenta. Web Lead actualizara
ambos environments, verificara JWE y publicara solo con autorizacion independiente.
No usar un proxy localhost ni cambiar `dataAccessMode` a mocks para ocultar fallos.
Android necesita coordinacion propia para su URL/issuer y sesiones nuevas.
Cerrar sesiones del consumidor y autenticar con cuentas nuevas: los refresh tokens
del origen no sirven en la reconstruccion vacia. No prometer recuperar esas cuentas.

CORS: origen exacto `https://jazmincorandes.github.io` (sin `/solventa-web-pf2/`),
Bearer sin cookies; rutas `/v1`. Terraform ya declara GET/POST/PUT/DELETE/OPTIONS,
cabeceras authorization/content-type/idempotency-key/x-correlation-id/api-version/
upload-offset y exposicion de date/retry-after/api-version/location/x-correlation-id.
Verificar preflight y cabeceras en respuestas reales 2xx/4xx/429, incluido acceso
desde JavaScript a Date y Retry-After; OPTIONS200 no acredita estas respuestas.
No cambiar DNS/CORS/certificados hoy ni publicar HTTP o desactivar validacion TLS.

## 7. Rollback

Guardar digests, revisiones/configuracion no secreta, resultados y planes aprobados.
Si falla antes del cutover, mantener el frontend sin anunciar el backend nuevo,
detener aprobaciones y diagnosticar conservando DB/buckets/Secrets. Un primer
Deployment no tiene version sana anterior: `rollout undo` no sustituye recuperacion.

Tras el cutover, cerrar el flujo afectado y revertir solo imagen/configuracion
compatible en el entorno nuevo. Conservar tablas aditivas, pagos, Outbox, trabajos
y evidencias; no ejecutar downgrade ni destruir la infraestructura para recuperar.
Revertir JWE de forma coordinada con web segun su runbook; no servir web cifrada
contra un backend vuelto a plaintext. Suspender CronJobs afectados solo con
autorizacion y registrar su reanudacion. No volver al endpoint origen sin comprobar
su funcionamiento y obtener autorizacion; hoy no se ha recuperado ese origen.
