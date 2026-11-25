module "kms" {
  source = "../../../modules/kms"
  name   = var.name_prefix
  tags   = local.tags

  depends_on = [data.aws_caller_identity.deployment, data.aws_vpc.network, data.aws_security_group.network]
}

module "eks" {
  source                   = "../../../modules/eks"
  permissions_boundary_arn = var.permissions_boundary_arns.network
  cluster_name             = "${var.name_prefix}-eks"
  kubernetes_version       = var.kubernetes_version
  vpc_id                   = var.network_context.vpc_id
  private_subnet_ids       = var.network_context.private_subnet_ids
  public_access_cidrs      = var.public_access_cidrs
  admin_principal_arns     = [var.deployment_role_arn]
  node_instance_types      = ["t3.small"]
  node_min_size            = var.node_count
  node_desired_size        = var.node_count
  node_max_size            = var.node_count
  additional_node_groups = merge(var.enable_medium_node_group ? {
    medium = {
      instance_types  = ["t3.medium"]
      release_version = var.medium_node_release_version
    }
    } : {}, var.enable_small_node_group ? {
    small-extra = {
      instance_types  = ["t3.small"]
      release_version = var.small_node_release_version
    }
  } : {})
  tags = local.tags

  depends_on = [data.aws_caller_identity.deployment, data.aws_vpc.network, data.aws_security_group.network]
}

module "database" {
  source                     = "../../../modules/rds-postgres"
  name                       = var.name_prefix
  identifier                 = var.name_prefix
  database_name              = "solventa_prod"
  vpc_id                     = var.network_context.vpc_id
  subnet_ids                 = var.network_context.private_subnet_ids
  existing_security_group_id = var.network_context.security_group_ids.db
  allowed_security_group_ids = [module.eks.node_security_group_id]
  kms_key_arn                = module.kms.key_arn
  instance_class             = "db.t4g.micro"
  allocated_storage          = 20
  multi_az                   = false
  deletion_protection        = true
  skip_final_snapshot        = false
  tags                       = local.tags
}

module "redis" {
  source                     = "../../../modules/elasticache-redis"
  name                       = var.name_prefix
  vpc_id                     = var.network_context.vpc_id
  subnet_ids                 = var.network_context.private_subnet_ids
  existing_security_group_id = var.network_context.security_group_ids.redis
  allowed_security_group_ids = [module.eks.node_security_group_id]
  kms_key_arn                = module.kms.key_arn
  node_type                  = "cache.t4g.micro"
  tags                       = local.tags
}

module "jwt_key" {
  source      = "../../../modules/jwt-signing-key"
  secret_name = "${var.name_prefix}-identidad-jwt"
  kms_key_arn = module.kms.key_arn
  tags        = local.tags
}

resource "aws_secretsmanager_secret" "database_domains" {
  name                    = "${var.name_prefix}-database-domains"
  description             = "Credenciales nuevas por dominio; valor administrado fuera de Terraform"
  kms_key_id              = module.kms.key_arn
  recovery_window_in_days = 7
  tags                    = local.tags
}

module "identidad_irsa" {
  source                   = "../../../modules/irsa-service"
  permissions_boundary_arn = var.permissions_boundary_arns.application
  name                     = "${var.name_prefix}-identidad"
  oidc_provider_arn        = module.eks.oidc_provider_arn
  oidc_issuer_url          = module.eks.oidc_issuer_url
  service_namespace        = "identidad"
  service_account_name     = "identidad"
  secret_arns              = [module.redis.secret_arn, module.jwt_key.secret_arn]
  kms_key_arn              = module.kms.key_arn
  tags                     = local.tags
}

module "api_gateway" {
  source             = "../../../modules/api-gateway"
  name               = var.name_prefix
  vpc_id             = var.network_context.vpc_id
  private_subnet_ids = var.network_context.private_subnet_ids
  existing_security_group_ids = {
    alb      = var.network_context.security_group_ids.alb
    vpc_link = var.network_context.security_group_ids.vpc_link
  }
  target_security_group_id = module.eks.node_security_group_id
  listener_protocol        = var.api_listener_protocol
  certificate_arn          = var.api_certificate_arn
  tls_server_name          = var.api_tls_server_name
  jwt_audience             = "solventa-api"
  enable_jwt_routes        = var.enable_jwt_routes
  cors_allowed_origins     = ["http://localhost:4200"]
  cors_expose_headers      = ["date", "retry-after", "x-reporte-huella", "x-content-sha256", "x-document-content-sha256"]
  tags                     = local.tags

  depends_on = [terraform_data.tls_configuration]
}

resource "terraform_data" "tls_configuration" {
  lifecycle {
    precondition {
      condition = var.api_listener_protocol != "HTTPS" || (
        var.api_certificate_arn != null && var.api_tls_server_name != null
      )
      error_message = "HTTPS interno requiere certificado ACM y nombre TLS verificable aprobados."
    }
  }
}

data "aws_iam_policy_document" "alb_controller_assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [module.eks.oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${replace(module.eks.oidc_issuer_url, "https://", "")}:sub"
      values   = ["system:serviceaccount:kube-system:aws-load-balancer-controller"]
    }
    condition {
      test     = "StringEquals"
      variable = "${replace(module.eks.oidc_issuer_url, "https://", "")}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "alb_controller" {
  name                 = "${var.name_prefix}-alb-controller"
  assume_role_policy   = data.aws_iam_policy_document.alb_controller_assume.json
  permissions_boundary = var.permissions_boundary_arns.network
  tags                 = local.tags
}

resource "aws_iam_policy" "alb_controller" {
  name   = "${var.name_prefix}-alb-controller"
  policy = file("${path.module}/../../../modules/iam-irsa/alb-controller-policy.json")
  tags   = local.tags
}

resource "aws_iam_role_policy_attachment" "alb_controller" {
  role       = aws_iam_role.alb_controller.name
  policy_arn = aws_iam_policy.alb_controller.arn
}

resource "aws_codestarconnections_connection" "github" {
  count         = length(var.pipeline_services) > 0 ? 1 : 0
  name          = "${var.name_prefix}-github"
  provider_type = "GitHub"
  tags          = local.tags

  depends_on = [data.aws_caller_identity.deployment]
}

module "cicd" {
  for_each = var.pipeline_services
  source   = "../../../modules/cicd"

  permissions_boundary_arn   = var.permissions_boundary_arns.ci
  name                       = var.name_prefix
  service_name               = each.key
  resource_prefix            = "${var.name_prefix}-${each.key}"
  github_connection_arn      = aws_codestarconnections_connection.github[0].arn
  create_github_connection   = false
  github_repository          = "Migue765/backend-solventa"
  branch_name                = var.release_branch
  release_tag_patterns       = ["${each.key}-v*"]
  run_migrations             = each.key != "bff-web"
  automatic_triggers_enabled = false
  require_manual_approval    = true
  restrict_service_access    = true
  artifact_force_destroy     = false
  vpc_id                     = var.network_context.vpc_id
  private_subnet_ids         = var.network_context.private_subnet_ids
  existing_security_group_id = var.network_context.security_group_ids.cicd[each.key]
  cluster_name               = module.eks.cluster_name
  cluster_security_group_id  = module.eks.node_security_group_id
  service_namespace          = each.key
  ecr_repository_arn         = module.ecr[each.key].repository_arn
  ecr_repository_url         = module.ecr[each.key].repository_url
  tags                       = merge(local.tags, { Service = each.key })
}
