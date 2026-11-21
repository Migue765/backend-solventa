# Productivo pequeno: primer recorrido integrado

**Entorno real autorizado y desplegado; no laboratorio ni entorno paralelo.**
Destino exclusivo: `096571798698/us-east-2`. No se reutiliza VPC default,
infraestructura, credenciales, datos ni state de la cuenta anterior.

## Solucion preparada

```text
Frontend local http://localhost:4200
 -> HTTPS API Gateway HTTP API
    -> VPC Link v2
       -> ALB interno, TG tipo IP
          -> BFF Web -> Identidad -> RDS PostgreSQL / Redis TLS
                     -> Perfilamiento -> la misma base PostgreSQL
          -> Identidad: discovery y JWKS publicos
```

La entrada funcional externa siempre pasa por Gateway. BFF y dominios usan
ClusterIP/DNS privados; no se crean NodePort, ALB publico ni servicios demo.
La API Kubernetes de gestion mantiene acceso privado y acceso publico restringido
a IPv4 /32 aprobadas: no confundirla con la entrada de usuarios.

| Root | Recursos aprovisionados |
|---|---|
| `bootstrap` | Bucket state nuevo, versionado, cifrado, TLS obligatorio, bloqueo publico y prevent_destroy. State inicial local protegido. |
| `network` | VPC propia, cuatro subnets en dos AZ, IGW, una NAT/IPv4, rutas, endpoint S3 y trece SG sin reglas propias. |
| `primary` | Nueve ECR, EKS con cuatro nodos compartidos (dos originales y dos small-extra), una RDS PostgreSQL, Redis TLS, CMK/JWT, IRSA Identidad/controlador, HTTP API/VPC Link/ALB y cuatro pipelines seleccionados. |
| `addons` | AWS Load Balancer Controller y metrics-server, despues del cluster. |

`network_context` se valida por cuenta, region, prefijo, pertenencia real de VPC/
subnets/SG, nombres de SG, Tier, ausencia de VPC default y dos AZ coincidentes.
Los tres states remotos usan keys distintas bajo
`accounts/096571798698/us-east-2/`. Ningun root lee state de otro.

Los ajustes de modulos compartidos conservan los contratos anteriores: cabeceras CORS
expuestas configurables, conservando el default anterior, y seleccion explicita
de creacion de conexion GitHub para CI cuando su ARN aun es desconocido en plan.
Esto evita recurrir a `-target` para una conexion creada en el mismo primary.
Los roots historicos conservan sus guardas y no se aplican.
El listener privado responde OPTIONS con 204 a prioridad 1, sin depender de pods.
Gateway agrega CORS y mantiene publicos solo los preflights/rutas de autenticacion;
las rutas funcionales siguen usando JWT.

## Decisiones efectivas y limites

| Tema | Estado |
|---|---|
| Rol ejecutor | `solventa-infra-deploy`, asumido exclusivamente por admin; perfil `solventa-infra` con source_profile `solventa-admin`. Sin nuevo usuario humano ni uso de IAM directo en los roots. Trust sin condicion MFA; no atribuirle esa garantia. |
| Boundaries | `solventa-prod-boundary-network`, `-ci`, `-application`, distintas y con permisos por funcion. No son AdministratorAccess. El admin conserva capacidad de modificarlas: NO acreditan aislamiento ni aprobacion independiente. |
| TLS interno | HTTP dentro de VPC privada autorizado expresamente. Gateway exterior HTTPS y RDS verify-full/Redis TLS. Pasar ALB a HTTPS exige certificado ACM/nombre verificable aprobados; JWE no reemplaza TLS. |
| WAF | No desplegado: HTTP API no tiene asociacion WAF nativa. Sigue siendo brecha frente al PDF; no se declara cumplido. Una proteccion adicional requiere diseno/coste explicitos. |
| Disponibilidad y coste | Unico productivo pequeno, una replica/API sobre cuatro t3.small compartidos, RDS/Redis single-AZ y una NAT. No HA, DR multirregional ni ausencia de riesgo/costes. HPA min=max=1, sin Cluster Autoscaler. |
| Ejecucion/CI | Bootstrap/red/primary/addons aplicados sin -target ni destrucciones de plataforma. EKS 1.35 en soporte estandar, PostgreSQL 17 y charts 3.6.0/3.14.0 reales. Conexion GitHub PENDING; builds iniciales reales CodeBuild con fuente S3 del worktree, no pipeline desde GitHub acreditado. |

El codigo rechaza root, IAM directo, otra cuenta/region, otro rol ejecutor,
boundaries vacias/comunes/de otra cuenta y HTTPS sin certificado/nombre.
La validacion sintactica de ARN no es una aprobacion de permisos ni de contenido.
CNI y controlador conservan sus policies existentes. El despliegue detecto 403
de ECR por boundary sobre las imagenes oficiales de EKS; se permitio solo lectura
en `us-east-2/602401143452` de `amazon-k8s-cni`, `amazon-k8s-cni-init`,
`amazon/aws-network-policy-agent`, `eks/coredns` y `eks/kube-proxy`.
No se quitaron boundaries ni se concedio escritura a registros externos.

## Persistencia y configuracion

Una instancia RDS privada, una base logica `solventa_prod`, siete esquemas de
dominio. Los dos BFF no usan DB. Redis es efimero para seguridad, no una segunda
base relacional de negocio. No se restauran dumps, snapshots ni fixtures.

`deploy/nueva-cuenta/database.sql` prepara, sobre una base vacia verificada,
owners NOLOGIN, runtime sin DDL ni membresia de owner y migradores separados.
Falla si ya existen esquemas/roles previstos o tablas publicas. No crea otra
base ni borra datos. Usa una transaccion para DDL y despues `\password` interactivo
sin eco para catorce logins. Si se interrumpe esa fase, completar los passwords
pendientes individualmente; no reejecutar DDL ni permitir passwords vacias.
El bootstrap ya se ejecuto en RDS nuevo mediante un pod operador temporal,
con settings montados como Secret y eliminado junto con Secret/ConfigMap al
terminar. Los catorce passwords nuevos se guardaron en Secrets Manager fuera del
state; no se ejecutaron los metacomandos interactivos de psql desde Python.
Se comprobaron owners, runtime sin CREATE/membresia owner y TLS.

Ejecutarlo solo desde un cliente privado autorizado en la VPC, con psql 17,
host comparado con `database_context`, CA oficial RDS, `sslmode=verify-full`,
base exacta y usuario administrador de esa DB. Usar prompt de password/canal
protegido, nunca argumentos con password ni un Deployment de aplicacion con el master.
No usar `scripts/local_database.py` contra RDS.

Los DSN usan `postgresql+psycopg://`, credenciales codificadas para URL, CA/TLS
verificados y el mismo nombre de base. Los migradores incluyen
`options=-crole=<dominio>_owner`; verificar current_database/current_schema/
session_user/current_user antes de migrar. Los seis historiales Alembic y
Perfilamiento init-db se conservan; no fusionarlos ni reiniciarlos.

| Namespace | Secrets requeridos para el primer recorrido |
|---|---|
| identidad | `identidad-config`: DATABASE_URL runtime, REDIS_URL rediss, JWT_PRIVATE_KEY; `identidad-migrator`: DATABASE_URL migrador; `profiling-internal`: INTERNAL_API_TOKEN; `identidad-login-encryption`: LOGIN_ENCRYPTION_PRIVATE_KEY. |
| perfilamiento | `perfilamiento-runtime`: PERFILAMIENTO_DATABASE_URL runtime e INTERNAL_API_TOKEN; `perfilamiento-migrator`: PERFILAMIENTO_DATABASE_URL migrador. |
| bff-web | `profiling-internal`: INTERNAL_API_TOKEN; sin acceso DB, Redis o RSA privada. |

El token interno tiene los mismos bytes, nuevos y sin salto de linea, en los tres
namespaces. Clave JWE persistente nueva, independiente de JWT. Entrega por archivos
protegidos fuera de Git y `kubectl create secret --from-file` con dry-run canalizado
a apply autorizado; no `--from-literal`, dumps de Secrets ni stdout del master.
IRSA no inyecta valores automaticamente; no se instala External Secrets/CSI.
La clave JWT y el token Redis del modulo existente quedan en el state:
protegerlo y no exportar outputs sin nombre ni JSON de planes.

## Secuencia reproducible; no resembrar el entorno activo

1. Verificar identidad STS del perfil de rol y de backend antes de init y antes
   de apply. Aprobar bootstrap y red por separado; conservar state local inicial.
   Completar los ejemplos con recursos reales, sin `init -migrate-state`, imports historicos,
   `-target`, `remote_state` o referencias a la cuenta anterior.
2. Exportar **solo** `network_context` de network y envolverlo bajo esa clave en
   `primary/network.auto.tfvars.json`. Completar variables obligatorias del primary,
   revisar plan protegido e inventario/coste reales antes de aplicar.
   Si los nueve ECR ya se crearon por solicitud directa, confirmar sus ARN/configuracion
   y activar `adopt_created_ecr=true`: los imports declarativos adoptan repositorios
   y lifecycle exclusivamente en este state nuevo, sin intentar recrearlos.
   No importar recursos de otra cuenta ni estados historicos.
   En el primer arranque, `enable_jwt_routes=false`: AWS valida discovery al crear
   el authorizer. El flag deja authorizer y proxies protegidos AUSENTES, no NONE.
3. Exportar **solo** `cluster_addons`, envolverlo bajo esa clave para
   `addons/terraform.tfvars.json`; revisar y autorizar addons por separado.
   Esperar controller/CRD y metrics-server, sin confundir eso con aceptacion.
4. Construir/publicar imagenes de los SHA aprobados, cada API en su ECR. Autorizar
   la conexion GitHub si se crean pipelines. Retener imagenes necesarias para
   rollback; la limpieza ECR por defecto conserva diez. Mantener Deploy pendiente.
5. Exportar **solo** `deployment_context` y `database_context`. Crear base/esquemas/
   roles nuevos por el procedimiento revisado; preparar Secrets privados.
   No imprimir `secret_arns` como si fueran sus valores, ni copiar el master a runtime.

El renderizador genera configuracion real a partir de metadatos e imagenes aprobados,
sin llamar AWS, kubectl ni un servidor. Desde la raiz del repo:

```powershell
python scripts\render_new_account.py --context RUTA_PRIVADA\deployment-context.json --images RUTA_PRIVADA\imagenes.json --output RUTA_PRIVADA\manifiestos
```

`imagenes.json` sigue `deploy/nueva-cuenta/images.json.example`, con las tres URI
reales `<ECR de su API>@sha256:<digest>`. Rechaza cuenta/region/issuer, imagenes
cruzadas, tags no fijados por digest y target groups ajenos. El directorio de
salida debe ser nuevo para no sobreescribir un renderizado previo.

6. Aplicar los `seed.json` de Perfilamiento, Identidad y BFF Web con contexto EKS
   explicito: incluyen namespaces, Service/SA y Deployments a **cero replicas**.
   No contienen HPA ni bindings. Preparar Secrets antes de crear Jobs.
7. Aplicar `perfilamiento/migration.json` y `identidad/migration.json`.
   Esperar cada Job por su nombre renderizado, comprobar head/tablas/owners y que
   runtime no puede DDL, SET ROLE owner, consultar historiales ni otros esquemas.
   Un Job terminado no sustituye esas comprobaciones. No aplicar todo el directorio.
8. Escalar Perfilamiento y despues Identidad/BFF Web a una replica. Esperar rollout
   y comprobar dependencias en privado. Aplicar post-migration de Identidad
   (HPA/PDB/Outbox) con Perfilamiento listo; BFF Web conserva su HPA/PDB a una replica.
   Las actualizaciones posteriores usan los pipelines existentes, sin sobrescribir
   flags de runtime ni los Secrets con Deployments completos.
9. Aplicar solo `bindings/identidad.json` para discovery/JWKS. Mantener BFF Web y
   BFF Movil sin targets mientras se preparan ambos flags JWE con
   `scripts/configure_encrypted_login.py`, contexto e imagenes exactas.
   Seguir `docs/operacion-login-cifrado.md`: prepare/enable Identidad, luego BFF,
   verify de ambos y flags true. Verificar claves distintas y Redis SET NX EX/GETDEL.
   Helper usa acceso privado; no requiere abrir el BFF. Los TG deben estar vacios,
   no simplemente unhealthy (ALB puede fallar abierto ante targets unhealthy).
10. Comprobar discovery/JWKS por el endpoint real y el issuer exacto; cambiar
    `enable_jwt_routes=true` y aplicar primary: crea authorizer y dos proxies JWT.
    Solo despues de ese cierre criptografico, aplicar `bindings/bff-web.json`.
    Esperar targets healthy y apuntar el frontend local al `api_endpoint` real.
    No abrir el binding Movil ni habilitar biometria como efecto de esta fase.

## Aceptacion del primer recorrido

El Gateway queda con origen unico `http://localhost:4200`, metodos
GET/POST/PUT/DELETE/OPTIONS; Authorization, Content-Type, Idempotency-Key,
API-Version, Upload-Offset y X-Correlation-ID permitidos; Date/Retry-After
expuestos; sin cookies. Preflight es publico, proxies funcionales conservan JWT.
Los desafios publicos de login/registro y discovery ya provienen del modulo real.

Recorrido ya acreditado por Gateway: desafios 200/no-store, registro sintetico
JWE 201 sin tokens, login 200, sesion JWT 200/sin token401, plaintext/replay400,
refresh200, logout204 y refresh revocado401. RDS conserva usuario/consentimiento y
el mismo acuse entre Identidad/Perfilamiento; TLS verify-full, owners NOLOGIN y
runtime sin DDL ni lectura de `identidad.alembic_version_identidad`.
El coordinador tambien comprobo la UI real: login, navegacion, reload/refresh,
logout y guard. Credenciales solo en un archivo local con ACL del usuario,
fuera de Git/mensajes/logs; unicamente rol cliente sintetico.

Aceptacion ampliada no ejecutada en este corte: consentimiento autorizado,
duplicado409, issuer incorrecto, fallo Redis503 sin fallback, aislamiento entre
clientes y reinicio del pod con login persistido. No atribuir esas evidencias a
la prueba de un unico cliente con consentimiento rechazado.
Conservar correlacion sin registrar passwords, JWT, PEM o DSN.

`/ping` no acredita autenticacion, persistencia ni integracion de negocio.
Los menus que dependen de otras APIs no estan entregados por este corte.

## Ampliacion sin cambiar arquitectura

Los nueve ECR, SG por pipeline y contrato de siete esquemas ya existen.
Cotizacion ya esta activa en el mismo corte: pipeline exclusivo, imagen con CA
RDS, migracion del esquema cotizacion y Secrets separados.
Su configuracion actual usa PostgreSQL y Perfilamiento real; no requiere S3/SNS
ni permisos AWS en runtime para dashboard/productos/destinos. Su ServiceAccount
no hereda el IRSA de Identidad y no monta token Kubernetes.
El renderizador admite su imagen opcional por digest y produce sus HPA/PDB.
Cotizacion permanece ClusterIP: BFF la consume internamente, sin abrir el TG
por defecto a rutas directas que no necesita este recorrido.
No reaplicar seeds de los servicios activos porque los pondria a cero.

Recorrido por Gateway ya comprobado con el mismo actor cliente:
`GET /v1/clientes/me/dashboard`, `/v1/productos-seguro` y `/v1/destinos-viaje`
devuelven 200; ocho productos y 34 destinos reales del catalogo versionado.
Cotizacion usa `solventa_prod.cotizacion`, TLS verify-full, owner NOLOGIN,
runtime sin CREATE ni lectura de `cotizacion.alembic_version`.

Digests del corte inicial en el registro
`096571798698.dkr.ecr.us-east-2.amazonaws.com/solventa/<api>`:

| API | Digest SHA256 |
|---|---|
| identidad | `0fc7029c22b7ef2fae28a828e0e07f5448a9578e5b269de9fd11d3149f1e8686` |
| perfilamiento | `1dae0db3da0ef966bd89e52b6c6bfb33e7351bb022ea7bde9f041b41005bda85` |
| bff-web | `93bbd4444a3edc85cd1a860ede73bca4d3b19fb1defaf7434df4c60d7e1600b8` |
| cotizacion | `79b1e16c90b3338c760c5e05573129530c2f5d8bf9e36f81ca26069a6081ab1d` |

Las tres primeras imagenes provienen del bundle SHA256
`fbcd588707bae771175d6fa75a2f683942d89d9fdcbe7b4477c0b62e9c0c46ad`;
Cotizacion de `654b94dfa9feb15acb0ddbbc24005f38c5d88bf89f71fdea3e3c5f5548dcdbd8`.
No confundir esos hashes con un commit: los bundles capturaron el contenido
tracked actual del worktree, con revision base Git
`5bcf454fdb54bf374acfe3982c2206d63367e0f3`; no se hicieron commits/push.
Antes de habilitar Source/Deploy, reconciliar estos cambios y la rama aprobada;
autorizar la conexion GitHub no demuestra que esa rama contenga este codigo.

Polizas ya esta integrada; su array procede del esquema real y su ausencia o
fallo devuelve 503, nunca un descriptor legado ni un vacio artificial.
Incorporar despues Notificaciones, sin fabricar resultados: esa seccion conserva
`disponibilidad=no_implementado`, que no equivale a entrega.
Extender `pipeline_services` solo junto con dependencias (hoy admite las cuatro
APIs preparadas y rechaza las demas).

Despues incorporar Siniestros con bucket/IRSA y sus dos workers, Asistencias con
Polizas/Siniestros y BFF Movil con su validacion privada antes del binding.
No crear otro cluster, gateway, RDS ni base por API. Reutilizar manifests, Jobs,
deploy.sh, imagen por servicio y sus contratos. Crear siete esquemas hoy no
significa que esas APIs ya esten desplegadas.

Antes de pasar de tres a nueve APIs, medir maxPods/ENI/IP, allocatable/requests y
CPU burst credits. El baseline de tres t3.small del runbook historico es una
referencia, no garantia de holgura en esta cuenta: contar nueve APIs, cuatro pods
worker adicionales, sistema, Jobs/CronJobs y maxSurge. Rollouts y Jobs secuenciales.
El modulo EKS ignora desired_size despues del alta: al ampliar, coordinar el
cambio del deseado con min/max y verificar el ASG; no asumir autoscaler instalado.

La ampliacion autorizada a cuatro small creo exclusivamente `small-extra` con
dos nodos, conservando los dos originales Ready y planificables. Ambos grupos
mantienen min/deseado/max de dos y release `1.35.9-20261119`; el intento medium
fallido y vacio fue eliminado con aprobacion separada.
Usar `enable_small_node_group=true` y la release observada segun el README;
mantener medium deshabilitado. La capacidad real es 5729,06 MiB allocatable
y 44 slots, parte ocupada por sistema. No supone redistribucion automatica:
el nodo original mas cargado seguia alrededor de 84% de RAM en la muestra.
El presupuesto conservador para completar dominios/workers y picos seriales
es 4864 MiB; quedan unos 865 MiB, no una garantia de carga o N-1.

## Coste previsto, no cotizacion ni tope

La tabla siguiente conserva la estimacion inicial de dos/tres nodos; tres ya
no es la alternativa recomendada para completar el backend por su margen RAM.
La capacidad actual de cuatro small y sus raices gp3 de 30 GiB cuesta
US$70,34/mes, US$35,17 adicionales frente a dos. Se consultaron precios
oficiales Ohio Linux/Shared/On-Demand: US$0,0208/h por small y US$0,08/GB-mes
gp3. No es el total de la cuenta. CPU T3 Unlimited excedente, impuestos y
demas servicios/consumos quedan aparte.

USD, 730 h/mes, On-Demand Linux, sin creditos/impuestos. Precios orientativos
de Ohio; verificar Calculator, plan/eligibilidad y soporte de versiones antes de crear.

| Base recurrente | Primer recorrido, 2 nodos | Nueve APIs, referencia 3 nodos |
|---|---:|---:|
| EKS soporte estandar | 73.00 | 73.00 |
| t3.small | 30.37 | 45.55 |
| NAT + IPv4 | 36.50 | 36.50 |
| ALB, sin LCU | 16.43 | 16.43 |
| RDS t4g.micro + Redis t4g.micro | 23.36 | 23.36 |
| EBS gp3 nodos + 20 GiB RDS gp2 | 7.10 | 9.50 |
| Subtotal base aproximado | 186.76 | 204.34 |

Orden de magnitud a baja carga, **sin limite garantizado**: primer corte 195-240
USD/mes; conjunto academico con nueve APIs aproximadamente 220-300 USD/mes.
La segunda banda incluye margen orientativo, no precio cerrado de todas las
integraciones ni cumplimiento del PDF.

Adicionales: LCU, Gateway, NAT por GB, cruces de AZ/internet, CPU credits,
KMS/Secrets, nueve ECR, logs/artefactos/backups, cuatro pipelines/ocho CodeBuild
iniciales frente a nueve/dieciocho al completar APIs, S3/evidencias/Object Lock,
SNS/SQS y trafico de sus recorridos. Almacenamiento y consumo del productivo no tienen tope automatico.
Reducir APIs apenas reduce el fijo EKS/NAT/ALB.
Apagar pods no apaga esos cargos ni RDS/Redis; alertas no detienen gasto.

Fuentes: [EKS](https://aws.amazon.com/eks/pricing/),
[VPC/NAT](https://aws.amazon.com/vpc/pricing/),
[ALB](https://aws.amazon.com/elasticloadbalancing/pricing/),
[Gateway](https://aws.amazon.com/api-gateway/pricing/).
EC2/RDS/Redis/discos son referencias que deben cotejarse en Calculator Ohio,
no una consulta autenticada a facturacion. No se promete gratuidad.

## Limites y rollback

Una replica, PDB minAvailable=1, RDS/Redis single-AZ y una NAT no cumplen HA,
warm standby multirregional, autoscaling coordinado ni SLA 99,97/99,99% de PDF.
Un segundo pod transitorio de maxSurge no es una segunda replica permanente.
PDB puede bloquear drains y no evita caidas involuntarias.

Si falla el corte, cerrar binding BFF, esperar cero targets registrados/drenando
y comprobar indisponibilidad antes de revertir flags/imagenes. Conservar imagenes
y claves, y no bajar migraciones como rollback de app. No destruir la nueva
infraestructura ni tocar el origen para un rollback operativo. Revisar los costes
que siguen corriendo mientras se investiga.

Las pruebas offline validan contratos, wiring, planes simulados y renderizado,
no latencia, salud real, contenido efectivo de boundaries ni persistencia en RDS.
