# Operación coordinada del login cifrado

Esta rama prepara código; no autoriza despliegues ni activa disparadores.
`cicd_automatic_triggers_enabled=false` se conserva. No ejecutar un `terraform apply`
general para habilitar esta función. `/ping` solo comprueba proceso, nunca Redis,
JWE, usuarios, gateway, migraciones o autenticación de socios.

## Requisitos antes del cambio

- Revisiones externas y CI verdes para el SHA exacto de backend y de web.
- Imágenes inmutables de identidad y BFF-web del mismo SHA; guardar las imágenes
  previas y revisiones de ambos Deployments y la versión previa de web.
- Migraciones hacia delante: identidad `0005_usuarios_socios.py` y cotización
  `0012_admision_socios.py`.
  Ejecutar y comprobar los Jobs correspondientes antes del corte; no bajar migraciones
  como parte del rollback de imágenes. Mantener el backend de socios, no sustituirlo
  por la versión antigua de main ni integrar SOL-201/open-finance.
- Redis real accesible desde identidad con `SET NX EX` y `GETDEL` (Redis >=6.2).
  Mantener la URL existente en `identidad-config/REDIS_URL`, sin copiarla a Git,
  argumentos, logs, CI ni archivos de evidencia.
- Crear **fuera de Terraform/state y del repositorio**, en namespace `identidad`,
  Secret dedicado `identidad-login-encryption` con campo
  `LOGIN_ENCRYPTION_PRIVATE_KEY`: PEM RSA privada >=2048 bits, persistente,
  independiente de `identidad-config/JWT_PRIVATE_KEY`. Custodiarla en el gestor de
  secretos aprobado y restaurarla idéntica entre pods/reinicios. No generarla en el
  arranque. No se requiere otra política IAM ni que el BFF tenga acceso a ella.
  Otro nombre se configura con `--secret-name` y, al renderizar manifiestos completos,
  `LOGIN_ENCRYPTION_SECRET_NAME`. Nunca pasar el PEM por argumentos.
- Publicar la excepción exacta `GET /v1/sesiones/desafio` hacia integración ALB
  existente, `authorization_type=NONE`, con límites
  `auth_throttling_rate_limit` / `auth_throttling_burst_limit`.
  Revisar un plan guardado limitado a la ruta y stage existentes; abortar si incluye
  cambios ajenos. Conservar JWT en `ANY /v1/{proxy+}` y `/movil/{proxy+}`.
  Mantener ALB prioridad 13 y CORS `API-Version` de socios. No exponer `/oauth/*`.
- Coordinar ventana o compuerta de tráfico web: el cambio de dos flags no es atómico.
  Mobile, renovación y registro no cambian; el registro sigue usando HTTPS sin JWE.

## Preparación explícita (no basta `set image`)

`deploy/codebuild/deploy.sh` actualiza imágenes pero no aplica estos Deployments.
El operador ejecuta `scripts/configure_encrypted_login.py` con **contexto explícito**
y referencias de imagen revisadas. Solo hace get/exec/patch/rollout en el Deployment
seleccionado. Antes de preparar identidad valida la clave del Secret en memoria
y la compara con el JWKS público actual; nunca imprime ni persiste el PEM.
No crea recursos, no modifica réplicas/HPA/RBAC/rutas y exige una réplica.
Cada patch usa `resourceVersion`; un cambio concurrente hace fallar la operación.
Las guardas son errores explícitos, también con `python -O`; no dependen de `assert`.
Guardar la revisión previa que imprime cada operación. No continuar ante un fallo.

Ejemplo desde raíz (sustituir marcadores, no ejecutar literalmente):

```text
python scripts/configure_encrypted_login.py prepare --context CONTEXTO --service identidad --image IMAGEN_IDENTIDAD_NUEVA --expected-image IMAGEN_IDENTIDAD_ANTERIOR --secret-name identidad-login-encryption
python scripts/configure_encrypted_login.py prepare --context CONTEXTO --service bff-web --image IMAGEN_BFF_NUEVA --expected-image IMAGEN_BFF_ANTERIOR
```

Ambos quedan con `ENCRYPTED_LOGIN_ENABLED=false`. La preparación de identidad
monta la referencia obligatoria al Secret y verifica en el pod la independencia
de claves, desafío TTL60, cifrado/descifrado JOSE y consumo real `GETDEL`,
con datos sintéticos sin autenticar ni crear usuarios. La referencia opcional del
manifiesto base solo permite conservar despliegues con función apagada sin Secret.

## Activación

1. Validar previamente ruta pública, migraciones y disponibilidad de imágenes/web.
2. Cerrar tráfico de inicio de sesión web durante el corte (no mobile).
3. Ejecutar `enable --context CONTEXTO --service identidad --image IMAGEN_IDENTIDAD_NUEVA`.
   Usa el mismo prefijo `python scripts/configure_encrypted_login.py`.
4. Ejecutar `enable --context CONTEXTO --service bff-web --image IMAGEN_BFF_NUEVA`.
   Primero comprueba desafío upstream; tras rollout comprueba la ruta local del BFF,
   no `/ping`. No hay fallback a contraseña en claro con los flags activos.
5. Publicar web compatible y comprobar por gateway público: desafío 200 `no-store`,
   cabeceras/JWK públicas sin material privado, login sintético de una cuenta
   **no productiva autorizada**, reuso 400, credencial incorrecta 401, fallos Redis 503
   en entorno aislado. Verificar renovación/mobile/registro y rol `socio`/`socios`.
   El script no reemplaza estas pruebas ni acredita conformance de producción.
6. Abrir tráfico solo tras el conjunto completo. Repetir `verify` en ambos servicios
   con `--context`, `--service` e `--image` exactos para diagnóstico sin cambios.

## Rollback coordinado

Cerrar login web; restaurar web anterior y flags **ambos false**, empezando por BFF:
`disable --context CONTEXTO --service bff-web --image IMAGEN_BFF_NUEVA` y después
`disable --context CONTEXTO --service identidad --image IMAGEN_IDENTIDAD_NUEVA`.
Restaurar imágenes/revisiones previas compatibles con socios mediante
`kubectl --context CONTEXTO -n SERVICIO rollout undo deployment/SERVICIO --to-revision=REVISION`
y comprobar `rollout status` para cada uno. Si falla `prepare`/`enable`, usar la revisión
impresa **antes de esa operación**; no usar `rollout undo` a ciegas entre múltiples etapas.
No revertir a plaintext mientras la web cifrada siga sirviéndose.

No borrar Secret ni rotar RSA durante rollback; esperar al menos 60s para desafíos en
vuelo. Conservar migraciones y pausa de pipelines. La ruta de desafío puede quedar
declarada: con flags false responde 503 y no expone credenciales.
Un fallo de rollout/preflight se propaga; el script no anuncia éxito ni oculta errores,
y no intenta decidir automáticamente el estado de la web.

## Evidencia Redis aislada

La suite `test_encrypted_login.py` usa fakeredis y cubre frontera JOSE, errores,
replay y concurrencia HTTP. `test_encrypted_login_real_redis.py` es opt-in con
`TEST_REDIS_URL=redis://127.0.0.1:6379/15` o servicio CI `redis`, sin acceso a Redis AWS.
CI Calidad provisiona `redis:7.2`. Comprueba TTL60, vencimiento real acelerado con
PEXPIRE, replay y ocho consumos concurrentes con exactamente un ganador. Solo elimina
sus claves de desafío aleatorias; nunca FLUSHDB/FLUSHALL. Sin esa URL se omite
explícitamente la prueba real; no confundir fakeredis o CI con evidencia AWS.
