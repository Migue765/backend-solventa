module "ecr_asistencias" {
  source = "../../../modules/ecr"

  repository_name = "solventa/asistencias"
  force_delete    = var.ecr_force_delete
  tags            = local.tags
}

module "cicd_asistencias" {
  source = "../../../modules/cicd"

  automatic_triggers_enabled = var.cicd_automatic_triggers_enabled

  name                      = var.name_prefix
  service_name              = "asistencias"
  resource_prefix           = "${var.name_prefix}-asistencias"
  github_connection_arn     = module.cicd.connection_arn
  github_repository         = var.github_repository
  branch_name               = var.release_branch
  release_tag_patterns      = ["asistencias-v*"]
  require_manual_approval   = var.require_manual_approval
  vpc_id                    = module.network.vpc_id
  private_subnet_ids        = module.network.private_subnet_ids
  cluster_name              = module.eks.cluster_name
  cluster_security_group_id = module.eks.node_security_group_id
  service_namespace         = "asistencias"
  ecr_repository_arn        = module.ecr_asistencias.repository_arn
  ecr_repository_url        = module.ecr_asistencias.repository_url
  tags                      = local.tags
}

output "asistencias_ecr_repository_url" {
  value = module.ecr_asistencias.repository_url
}

output "asistencias_pipeline_name" {
  value = module.cicd_asistencias.pipeline_name
}
