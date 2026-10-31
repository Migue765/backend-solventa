locals {
  tags = {
    Project     = "solventa"
    Environment = var.environment
    ManagedBy   = "terraform"
  }

  cluster_name = "${var.name_prefix}-eks"
}

module "network" {
  source = "../../../modules/network"

  name         = var.name_prefix
  vpc_cidr     = var.vpc_cidr
  az_count     = 2
  cluster_name = local.cluster_name
  tags         = local.tags
}

module "kms" {
  source = "../../../modules/kms"

  name = var.name_prefix
  tags = local.tags
}

module "ecr" {
  source = "../../../modules/ecr"

  repository_name = "solventa/cotizacion"
  force_delete    = var.ecr_force_delete
  tags            = local.tags
}

module "eks" {
  source = "../../../modules/eks"

  cluster_name         = local.cluster_name
  kubernetes_version   = var.kubernetes_version
  vpc_id               = module.network.vpc_id
  private_subnet_ids   = module.network.private_subnet_ids
  public_access_cidrs  = var.public_access_cidrs
  admin_principal_arns = var.eks_admin_principal_arns
  node_instance_types  = var.node_instance_types
  node_min_size        = var.node_min_size
  node_desired_size    = var.node_desired_size
  node_max_size        = var.node_max_size
  tags                 = local.tags
}

module "database" {
  source = "../../../modules/rds-postgres"

  name                       = var.name_prefix
  vpc_id                     = module.network.vpc_id
  subnet_ids                 = module.network.private_subnet_ids
  allowed_security_group_ids = [module.eks.node_security_group_id]
  kms_key_arn                = module.kms.key_arn
  identifier                 = var.name_prefix
  database_name              = var.db_name
  instance_class             = var.db_instance_class
  allocated_storage          = var.db_allocated_storage
  deletion_protection        = var.db_deletion_protection
  skip_final_snapshot        = var.db_skip_final_snapshot
  tags                       = local.tags
}

module "s3_evidence" {
  source = "../../../modules/s3"

  bucket_name                = "${var.name_prefix}-evidencias-${var.account_id}"
  kms_key_arn                = module.kms.key_arn
  object_lock_mode           = var.object_lock_mode
  object_lock_retention_days = var.object_lock_retention_days
  tags                       = local.tags
}

module "messaging" {
  source = "../../../modules/messaging"

  name        = var.name_prefix
  kms_key_arn = module.kms.key_arn
  tags        = local.tags
}

module "iam_irsa" {
  source = "../../../modules/iam-irsa"

  name                 = var.name_prefix
  oidc_provider_arn    = module.eks.oidc_provider_arn
  oidc_issuer_url      = module.eks.oidc_issuer_url
  service_namespace    = var.service_namespace
  service_account_name = var.service_account_name
  db_secret_arn        = module.database.master_user_secret_arn
  evidence_bucket_arn  = module.s3_evidence.bucket_arn
  sns_topic_arn        = module.messaging.topic_arn
  kms_key_arn          = module.kms.key_arn
  tags                 = local.tags
}

module "ecr_bff_web" {
  source = "../../../modules/ecr"

  repository_name = "solventa/bff-web"
  force_delete    = var.ecr_force_delete
  tags            = local.tags
}

module "redis" {
  source = "../../../modules/elasticache-redis"

  name                       = var.name_prefix
  vpc_id                     = module.network.vpc_id
  subnet_ids                 = module.network.private_subnet_ids
  allowed_security_group_ids = [module.eks.node_security_group_id]
  kms_key_arn                = module.kms.key_arn
  node_type                  = var.redis_node_type
  tags                       = local.tags
}

module "ecr_bff_movil" {
  source = "../../../modules/ecr"

  repository_name = "solventa/bff-movil"
  force_delete    = var.ecr_force_delete
  tags            = local.tags
}

module "ecr_identidad" {
  source = "../../../modules/ecr"

  repository_name = "solventa/identidad"
  force_delete    = var.ecr_force_delete
  tags            = local.tags
}

# Clave con la que identidad firma los JWT (RS256). Ver la nota de rotacion en el modulo.
module "jwt_key" {
  source = "../../../modules/jwt-signing-key"

  secret_name = "${var.name_prefix}-identidad-jwt"
  kms_key_arn = module.kms.key_arn
  tags        = local.tags
}

# identidad es el unico servicio nuevo con acceso a la base de usuarios, a Redis y a la clave JWT.
module "iam_irsa_identidad" {
  source = "../../../modules/irsa-service"

  name                 = "${var.name_prefix}-identidad"
  oidc_provider_arn    = module.eks.oidc_provider_arn
  oidc_issuer_url      = module.eks.oidc_issuer_url
  service_namespace    = var.identidad_namespace
  service_account_name = var.identidad_service_account_name
  secret_arns          = [module.database.master_user_secret_arn, module.redis.secret_arn, module.jwt_key.secret_arn]
  kms_key_arn          = module.kms.key_arn
  tags                 = local.tags
}

module "api_gateway" {
  source = "../../../modules/api-gateway"

  name                     = var.name_prefix
  vpc_id                   = module.network.vpc_id
  private_subnet_ids       = module.network.private_subnet_ids
  target_security_group_id = module.eks.node_security_group_id
  listener_protocol        = var.api_listener_protocol
  certificate_arn          = var.api_certificate_arn
  tls_server_name          = var.api_tls_server_name
  throttling_rate_limit    = var.api_throttling_rate_limit
  throttling_burst_limit   = var.api_throttling_burst_limit
  jwt_audience             = var.jwt_audience
  cors_allowed_origins     = var.cors_allowed_origins
  tags                     = local.tags
}

module "cicd" {
  source = "../../../modules/cicd"

  automatic_triggers_enabled = var.cicd_automatic_triggers_enabled

  name                      = var.name_prefix
  github_repository         = var.github_repository
  branch_name               = var.release_branch
  require_manual_approval   = var.require_manual_approval
  vpc_id                    = module.network.vpc_id
  private_subnet_ids        = module.network.private_subnet_ids
  cluster_name              = module.eks.cluster_name
  cluster_security_group_id = module.eks.node_security_group_id
  service_namespace         = var.service_namespace
  ecr_repository_arn        = module.ecr.repository_arn
  ecr_repository_url        = module.ecr.repository_url
  tags                      = local.tags
}

# Segundo pipeline (bff-web): reutiliza la conexion con GitHub de cotizacion, pero con
# bucket, roles y SG propios (resource_prefix) y un patron de tag disjunto del de cotizacion.
# No migra: los BFF no tienen base de datos (los usuarios los migra identidad).
module "cicd_bff_web" {
  source = "../../../modules/cicd"

  automatic_triggers_enabled = var.cicd_automatic_triggers_enabled

  name                      = var.name_prefix
  service_name              = "bff-web"
  resource_prefix           = "${var.name_prefix}-bff-web"
  github_connection_arn     = module.cicd.connection_arn
  run_migrations            = false
  github_repository         = var.github_repository
  branch_name               = var.release_branch
  release_tag_patterns      = var.bff_web_release_tag_patterns
  require_manual_approval   = var.require_manual_approval
  vpc_id                    = module.network.vpc_id
  private_subnet_ids        = module.network.private_subnet_ids
  cluster_name              = module.eks.cluster_name
  cluster_security_group_id = module.eks.node_security_group_id
  service_namespace         = var.bff_web_namespace
  ecr_repository_arn        = module.ecr_bff_web.repository_arn
  ecr_repository_url        = module.ecr_bff_web.repository_url
  tags                      = local.tags
}

# Tercer pipeline (bff-movil): misma conexion con GitHub, tag bff-movil-v* y sin migraciones.
module "cicd_bff_movil" {
  source = "../../../modules/cicd"

  automatic_triggers_enabled = var.cicd_automatic_triggers_enabled

  name                      = var.name_prefix
  service_name              = "bff-movil"
  resource_prefix           = "${var.name_prefix}-bff-movil"
  github_connection_arn     = module.cicd.connection_arn
  run_migrations            = false
  github_repository         = var.github_repository
  branch_name               = var.release_branch
  release_tag_patterns      = var.bff_movil_release_tag_patterns
  require_manual_approval   = var.require_manual_approval
  vpc_id                    = module.network.vpc_id
  private_subnet_ids        = module.network.private_subnet_ids
  cluster_name              = module.eks.cluster_name
  cluster_security_group_id = module.eks.node_security_group_id
  service_namespace         = var.bff_movil_namespace
  ecr_repository_arn        = module.ecr_bff_movil.repository_arn
  ecr_repository_url        = module.ecr_bff_movil.repository_url
  tags                      = local.tags
}

# Cuarto pipeline (identidad): tag identidad-v*, con migraciones (dueno de la tabla usuarios).
module "cicd_identidad" {
  source = "../../../modules/cicd"

  automatic_triggers_enabled = var.cicd_automatic_triggers_enabled

  name                      = var.name_prefix
  service_name              = "identidad"
  resource_prefix           = "${var.name_prefix}-identidad"
  github_connection_arn     = module.cicd.connection_arn
  github_repository         = var.github_repository
  branch_name               = var.release_branch
  release_tag_patterns      = var.identidad_release_tag_patterns
  require_manual_approval   = var.require_manual_approval
  vpc_id                    = module.network.vpc_id
  private_subnet_ids        = module.network.private_subnet_ids
  cluster_name              = module.eks.cluster_name
  cluster_security_group_id = module.eks.node_security_group_id
  service_namespace         = var.identidad_namespace
  ecr_repository_arn        = module.ecr_identidad.repository_arn
  ecr_repository_url        = module.ecr_identidad.repository_url
  tags                      = local.tags
}

# ---------------------------------------------------------------------------
# Controladores en el cluster (Helm)
# ---------------------------------------------------------------------------
resource "helm_release" "aws_load_balancer_controller" {
  name       = "aws-load-balancer-controller"
  namespace  = "kube-system"
  repository = "https://aws.github.io/eks-charts"
  chart      = "aws-load-balancer-controller"
  version    = var.alb_controller_chart_version

  set {
    name  = "clusterName"
    value = module.eks.cluster_name
  }

  set {
    name  = "region"
    value = var.aws_region
  }

  set {
    name  = "vpcId"
    value = module.network.vpc_id
  }

  set {
    name  = "serviceAccount.name"
    value = "aws-load-balancer-controller"
  }

  set {
    name  = "serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn"
    value = module.iam_irsa.alb_controller_role_arn
  }

  depends_on = [module.eks]
}

resource "helm_release" "metrics_server" {
  name       = "metrics-server"
  namespace  = "kube-system"
  repository = "https://kubernetes-sigs.github.io/metrics-server/"
  chart      = "metrics-server"
  version    = var.metrics_server_chart_version

  depends_on = [module.eks]
}
