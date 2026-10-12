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
  database_name              = "cotizacion"
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
  tags                     = local.tags
}

module "cicd" {
  source = "../../../modules/cicd"

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
