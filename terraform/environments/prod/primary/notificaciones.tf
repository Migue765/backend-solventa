module "ecr_notificaciones" {
  source = "../../../modules/ecr"

  repository_name = "solventa/notificaciones"
  force_delete    = var.ecr_force_delete
  tags            = local.tags
}

module "cicd_notificaciones" {
  source                   = "../../../modules/cicd"
  permissions_boundary_arn = var.permissions_boundary_arns.ci

  automatic_triggers_enabled = var.cicd_automatic_triggers_enabled

  name                       = var.name_prefix
  service_name               = "notificaciones"
  resource_prefix            = "${var.name_prefix}-notificaciones"
  github_connection_arn      = module.cicd.connection_arn
  github_repository          = var.github_repository
  branch_name                = var.release_branch
  release_tag_patterns       = ["notificaciones-v*"]
  require_manual_approval    = var.require_manual_approval
  vpc_id                     = var.network_context.vpc_id
  private_subnet_ids         = var.network_context.private_subnet_ids
  existing_security_group_id = var.network_context.security_group_ids.cicd.notificaciones
  cluster_name               = module.eks.cluster_name
  cluster_security_group_id  = module.eks.node_security_group_id
  service_namespace          = "notificaciones"
  ecr_repository_arn         = module.ecr_notificaciones.repository_arn
  ecr_repository_url         = module.ecr_notificaciones.repository_url
  tags                       = local.tags
}

output "notificaciones_ecr_repository_url" {
  value = module.ecr_notificaciones.repository_url
}

output "notificaciones_pipeline_name" {
  value = module.cicd_notificaciones.pipeline_name
}
