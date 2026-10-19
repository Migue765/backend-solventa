module "ecr_polizas" {
  source = "../../../modules/ecr"

  repository_name = "solventa/polizas"
  force_delete    = var.ecr_force_delete
  tags            = local.tags
}

module "cicd_polizas" {
  source = "../../../modules/cicd"

  name                      = var.name_prefix
  service_name              = "polizas"
  resource_prefix           = "${var.name_prefix}-polizas"
  github_connection_arn     = module.cicd.connection_arn
  github_repository         = var.github_repository
  branch_name               = var.release_branch
  release_tag_patterns      = ["polizas-v*"]
  require_manual_approval   = var.require_manual_approval
  vpc_id                    = module.network.vpc_id
  private_subnet_ids        = module.network.private_subnet_ids
  cluster_name              = module.eks.cluster_name
  cluster_security_group_id = module.eks.node_security_group_id
  service_namespace         = "polizas"
  ecr_repository_arn        = module.ecr_polizas.repository_arn
  ecr_repository_url        = module.ecr_polizas.repository_url
  tags                      = local.tags
}

output "polizas_ecr_repository_url" {
  value = module.ecr_polizas.repository_url
}

output "polizas_pipeline_name" {
  value = module.cicd_polizas.pipeline_name
}
