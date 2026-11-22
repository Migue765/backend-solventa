# Cuenta nueva: infraestructura y repositorios de las APIs

Destino exclusivo: `096571798698/us-east-2`, prefijo `solventa-prod`.
Existe un unico entorno productivo real, de tamano pequeno, creado con
autorizacion directa. No usar los backends, states ni recursos de la cuenta
anterior. Los roots historicos permanecen sin cambios. La
[runbook del primer recorrido](PRIMER-RECORRIDO.md) detalla bootstrap, red,
primary y addons, decisiones efectivas, secuencia de activacion privada,
persistencia unica, costes y criterios de aceptacion.

`primary` declara un repositorio **ECR privado por API**, reutilizando
`terraform/modules/ecr`:

| API | Repositorio |
|---|---|
| Identidad | `solventa/identidad` |
| BFF Web | `solventa/bff-web` |
| BFF Movil | `solventa/bff-movil` |
| Cotizacion | `solventa/cotizacion` |
| Perfilamiento | `solventa/perfilamiento` |
| Polizas | `solventa/polizas` |
| Siniestros | `solventa/siniestros` |
| Notificaciones | `solventa/notificaciones` |
| Asistencias | `solventa/asistencias` |

Cada repositorio conserva tags inmutables, escaneo al publicar, cifrado AES256 y
`force_delete=false`. El modulo retiene las diez imagenes mas recientes: antes de
publicar, revisar que la limpieza no retire una imagen activa o necesaria para
rollback. Los trabajadores y Jobs de Siniestros reutilizan su imagen, no la de
otra API.

El output `ecr_repositories` entrega un mapa por servicio con `name`, `arn` y
`url`. Los pipelines seleccionados usan exclusivamente la entrada de su API
para build, migraciones y Deployment. El primary tambien compone EKS, API Gateway,
una base relacional y las dependencias del primer recorrido, sin servicios demo.
Gateway real: `https://r92im9tgi6.execute-api.us-east-2.amazonaws.com`.
Identidad, BFF Web y Perfilamiento ya completaron registro/login JWE, JWT,
refresh/logout y persistencia/acuse entre esquemas en RDS. Cotizacion ya funciona
en el mismo cluster y base: dashboard/productos/destinos devuelven 200 por BFF,
con ocho productos y 34 destinos de su catalogo versionado, sin mocks.
Polizas tambien esta integrada en el mismo cluster y base. El dashboard entrega
`polizas[]` desde una consulta real; si la dependencia falla, responde 503 en
lugar de inventar vacios. Notificaciones conserva el descriptor
`{disponibilidad: "no_implementado"}` en ese contrato.
Quedan cuatro APIs por integrar; no se declara terminado todo el backend.

El plan de integracion puede agregar builds aislados para las APIs sin pipeline
mediante `bootstrap_build_sources`; cada rol solo lee su ZIP SHA256 y publica en
su propio ECR. No duplicar los cuatro proyectos que ya tienen pipeline ni omitir
la entrada existente de Polizas. Un build por snapshot no acredita un commit:
registrar por separado el SHA Git y el checksum del ZIP.

`enable_siniestros_evidence=true` propone un bucket privado versionado y un rol
IRSA exclusivo de `siniestros/siniestros`, limitado a `evidencias/*`. Reutiliza la
CMK existente, no el rol de Identidad, y no agrega nodos ni otra base de datos.
El output `siniestros_context` entrega solamente bucket y rol. El flag permanece
deshabilitado por defecto; revisar altas, cambios, costes y rollback antes de
aplicar. Los Secrets runtime/migrator de los dominios faltantes deben provenir del
Secret existente `solventa-prod-database-domains`, por operador autorizado, sin
regenerar usuarios ni imprimir valores. Estos recursos no despliegan los pods,
no ejecutan migraciones y no acreditan sus recorridos funcionales.
Las imagenes de Siniestros y Asistencias incluyen la CA RDS y
`PGSSLROOTCERT`, igual que los demas dominios PostgreSQL; conservar
`sslmode=verify-full` en los DSN runtime y migrator.

## Identidad y guardas

El usuario admin asume `arn:aws:iam::096571798698:role/solventa-infra-deploy`
mediante el perfil local `solventa-infra`, sin crear otro usuario IAM.
`deployment_role_arn` requiere el ARN IAM estable del rol aprobado; el caller debe
ser una sesion STS de ese mismo rol. Root, acceso IAM directo, otra cuenta,
otra region y otro rol se rechazan.

El rol, su trust exclusivo para admin y las tres boundaries por funcion fueron
establecidos en el paso inicial autorizado, fuera de estos roots.
Su trust no exige MFA. El perfil tiene `source_profile=solventa-admin`;
no usar el perfil directo como identidad ejecutora.
Un rol asumido no resuelve las boundaries externas ni impide que un administrador
con AdministratorAccess las modifique. Los roots historicos conservan sus gates; no se aplican ni se atribuye
aislamiento independiente al modelo de administracion elegido para esta cuenta.

Los ejemplos dejan vacios perfil, ARN y bucket. No completar con datos inventados.
El backend S3 lleva cuenta permitida y key nuevas; comprobar STS del perfil de
backend **antes de init**, ademas del perfil del provider antes de plan/apply.
No usar `init -migrate-state`, imports historicos, `-target`, `remote_state` ni copiar states.
`adopt_created_ecr=true` admite exclusivamente los nueve ECR y lifecycle nuevos
creados por solicitud directa, despues de comprobar sus ARN/configuracion;
los importa al state nuevo para evitar creacion duplicada.
Los planes y states del primary contienen secretos de JWT y Redis:
no imprimirlos ni versionarlos.

Bucket de state nuevo: `solventa-tfstate-096571798698`, con keys separadas por root.
El exterior usa HTTPS; HTTP interno privado fue autorizado expresamente.
ECR y los demas componentes generan costes; apagar pods no detiene los cargos.
La conexion `solventa-prod-github` sigue PENDING de autorizacion en consola:
los builds iniciales reales usaron CodeBuild con bundles S3 del worktree,
identificados por SHA256, sin commits/push ni acceso a datos anteriores.

## Transicion de capacidad por fases

### Ampliacion a cuatro small

`enable_small_node_group=true`, `node_count=2`,
`enable_medium_node_group=false` y `small_node_release_version` fijada a la
release observada agregan un grupo `small-extra` de dos `t3.small`.
El total pasa a cuatro sin actualizar, retirar ni cordonar los dos originales.
Se reutilizan el mismo EKS, launch template, disco gp3 cifrado de 30 GiB,
rol, boundaries y subnets; no cambia el plan de cuenta.

Antes de aplicar, revisar un plan con una sola alta:
`module.eks.aws_eks_node_group.additional["small-extra"]`, sin otros cambios.
Conservar las entradas privadas de red, management y bootstrap de Polizas.
No escalar editando solamente `desired_size`: el modulo lo ignora para
preservar la gestion externa del numero deseado. Cada grupo mantiene
minimo/deseado/maximo de dos nodos.

Con 730 h/mes y precios oficiales Ohio Linux/Shared/On-Demand, cuatro small
con sus raices cuestan aproximadamente US$70,34/mes, US$35,17 adicionales.
Excluir impuestos, excedentes CPU T3 Unlimited y otros servicios.
Verificar cuatro nodos Ready/planificables, RAM y slots reales, sistema y
recorrido por Gateway antes de integrar mas cargas. Los pods existentes no
se redistribuyen automaticamente. No supone HA ni capacidad N-1 garantizada.

### Alternativa medium no activa

`enable_medium_node_group=true`, con `node_count=2` y
`medium_node_release_version` fijada a la release observada, agrega dos
`t3.medium` sin modificar ni retirar los dos `t3.small`. Se reutilizan
launch template, disco gp3 cifrado de 30 GiB, rol, boundaries y subnets.
El flag permanece deshabilitado por defecto; no debe habilitarse sin revision
del coste de coexistencia y de la secuencia de migracion.

La fase A requiere un plan exclusivamente aditivo: un nodegroup nuevo y
ninguna destruccion/reemplazo del existente. Tras verificar capacidad real,
cordon de small y rollouts seriales con `maxSurge=1`, `maxUnavailable=0`,
readiness y comprobacion del recorrido por Gateway. Conservar HPA/PDB y
workers; esperar Jobs activos. El PDB no regula las eliminaciones del
controlador durante un rollout. No omitir evictions ni forzar un drain.
La retirada de small es una fase B distinta que requiere revision propia;
este flag no la implementa ni autoriza. No incorporar nuevas APIs/workers
durante la ventana de migracion.

Un plan valido y una cuota suficiente no acreditan que la cuenta permita
lanzar el tipo elegido. En esta cuenta, AWS rechazo los lanzamientos
`t3.medium` con `InvalidParameterCombination`: tipo no elegible para Free Tier.
No se migraron cargas ni se cordonaron los small. Revisar la restriccion de
cuenta antes de reintentar; aprobar presupuesto no autoriza cambiar el plan
de facturacion. No sustituir el tipo ni ampliar permisos/cuotas sin revision.

Con 730 h/mes, computo On-Demand Ohio y los discos raiz indicados, el
incremento final estimado es US$30,37/mes. Mientras conviven cuatro nodos,
el incremento frente a dos small es aproximadamente US$0,0898/h, incluidos
los discos adicionales. No incluye impuestos, creditos CPU T3 ni consumos
variables. No implica HA ni habilita autoscaling de nodos.

## Comprobaciones offline

Con Terraform y providers ya disponibles, usar `init -backend=false` y `validate`
en cada root. Desde la raiz, `python -m pytest tests\test_new_account.py -q`
ejecuta las pruebas de renderizado y genera el harness de planes Terraform con
`mock_provider` en un checkout temporal, usando un mirror filesystem de providers
ya instalados, sin descarga ni conexion a AWS. Sin esos providers omite explicitamente
el caso Terraform, no lo sustituye por exito. Las fixtures son exclusivamente
sinteticas. La inicializacion local no es el bootstrap del backend remoto.
