module "ecr_perfilamiento" {
  source = "../../../modules/ecr"

  repository_name = "solventa/perfilamiento"
  force_delete    = var.ecr_force_delete
  tags            = local.tags
}

module "cicd_perfilamiento" {
  source = "../../../modules/cicd"

  automatic_triggers_enabled = var.cicd_automatic_triggers_enabled

  name                      = var.name_prefix
  service_name              = "perfilamiento"
  service_dir               = "services/perfilamiento"
  resource_prefix           = "${var.name_prefix}-perfilamiento"
  github_connection_arn     = module.cicd.connection_arn
  run_migrations            = true
  github_repository         = var.github_repository
  branch_name               = var.release_branch
  release_tag_patterns      = ["perfilamiento-v*"]
  require_manual_approval   = var.require_manual_approval
  vpc_id                    = module.network.vpc_id
  private_subnet_ids        = module.network.private_subnet_ids
  cluster_name              = module.eks.cluster_name
  cluster_security_group_id = module.eks.node_security_group_id
  service_namespace         = "perfilamiento"
  ecr_repository_arn        = module.ecr_perfilamiento.repository_arn
  ecr_repository_url        = module.ecr_perfilamiento.repository_url
  tags                      = local.tags
}

output "perfilamiento_ecr_repository_url" {
  description = "URL del repositorio ECR de perfilamiento."
  value       = module.ecr_perfilamiento.repository_url
}

output "perfilamiento_pipeline_name" {
  description = "Pipeline de despliegue de perfilamiento (tag perfilamiento-v*)."
  value       = module.cicd_perfilamiento.pipeline_name
}
