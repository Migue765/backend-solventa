# Red previa a primary

Preparacion local para la reconstruccion vacia `396383602233/us-east-2`.
No autoriza crear recursos, estado, permisos ni ejecutar un plan conectado.
No usar estados de Jenkins/origen ni `-target`, import, state mv o migraciones.

Este root reutiliza el modulo network: 15 recursos de red declarados expandidos
(VPC, cuatro subnets, IGW, EIP/NAT, rutas/asociaciones y endpoint S3) y 13 SG
extraidos de primary: nueve CodeBuild, DB, Redis, ALB y VPC Link. Conserva sus
nombres/descripciones/tags. No contiene roles, secrets, KMS, EKS, bases ni pipelines.
**NAT/EIP empiezan a consumir antes de desplegar servicios**; falta autorizar costo.

Las reglas de los 13 SG siguen en primary, incluidos egress y enlaces al SG
automatico de EKS. Aqui no declarar reglas inline ni intentar administrar el SG
automatico; red sola no habilita conectividad de servicios. El default de
Perfilamiento sigue restringido; los otros ocho CI conservan su comportamiento.

## Contrato y orden, solo con autorizacion posterior por fase

1. Revalidar STS del rol asumido, cuenta/region, baseline Jenkins y bucket real del
   bootstrap. Completar `terraform.tfvars` y `backend.hcl` desde los ejemplos.
   Key exclusiva: `environments/prod/network/terraform.tfstate`; lock S3 propio.
   Un plan network solo necesita lectura de ESA key y operaciones sobre su lock,
   no escritura del state, apply, IAM ni las keys de primary/Jenkins.
2. Revisar/aplicar un plan NUEVO de network solo cuando se autorice. El root lee
   `aws_caller_identity` (STS) y el modulo existente `aws_availability_zones`
   (`ec2:DescribeAvailabilityZones`); `aws_region` usa configuracion local.
   No agrega data sources EC2 de VPC/subnets/SG ni lecturas del state de primary.
   Esto enumera los data sources, no sustituye revisar llamadas del provider
   durante un plan real autorizado. No resolver errores concediendo escrituras.
3. Leer unicamente `terraform output -json network_context`. Guardar ese objeto
   bajo la clave `network_context` en `../primary/network.auto.tfvars.json`
   (ignorado). No exportar todos los outputs ni copiar state. Revisar y conservar
   el objeto completo: cuenta, region, prefijo, VPC ID/ARN, dos subnets publicas,
   dos privadas y los 13 SG tipados. No inventar IDs ni usar ejemplos sinteticos.
4. Con esos IDs reales, Despliegue prepara boundaries/identidades externas por
   accion y subrecurso. Red primero no acredita aislamiento CNI/ALB/SLR ni
   existencia/eficacia de una boundary. Sin tags dinamicos de rol. Los topes por
   instancia requieren los controles de perfil/cluster y antirretag aprobados;
   los SLR no quedan limitados por boundaries de los roles cliente.
5. Primary exige el objeto y lee VPC/subnets/SG por ID para comprobar cuenta,
   pertenencia, nombres, Tier y dos AZ coherentes. Son lecturas nuevas de
   **primary**, no de network: DescribeVpcs, DescribeSubnets, DescribeSecurityGroups.
   No lee remote_state ni recrea la red/13 SG. Despues siguen addons y el
   [arranque vacio](../../../../deploy/k8s/ARRANQUE-VACIO.md).

Los planes/snapshots anteriores al split son invalidos. Ningun state se mueve
entre roots. En cuatro modulos compartidos (`cicd`, `rds-postgres`,
`elasticache-redis`, `api-gateway`) hay moved declarativos locales para conservar
el SG legacy al introducir count; API tiene dos SG. Sus defaults siguen creando
sus propios SG. No se comprobo un plan vivo legacy ni se ejecuto state mv.

No destruir network mientras primary/addons lo consuman. Un fallo de primary no
autoriza borrar red o tocar Jenkins: mantener la compuerta publica cerrada,
inspeccionar el plan nuevo y pedir la autorizacion de recuperacion correspondiente.
