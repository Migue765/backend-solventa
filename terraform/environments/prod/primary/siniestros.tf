module "ecr_siniestros" {
  source = "../../../modules/ecr"

  repository_name = "solventa/siniestros"
  force_delete    = var.ecr_force_delete
  tags            = local.tags
}

module "cicd_siniestros" {
  source = "../../../modules/cicd"

  automatic_triggers_enabled = var.cicd_automatic_triggers_enabled

  name                      = var.name_prefix
  service_name              = "siniestros"
  resource_prefix           = "${var.name_prefix}-siniestros"
  github_connection_arn     = module.cicd.connection_arn
  github_repository         = var.github_repository
  branch_name               = var.release_branch
  release_tag_patterns      = ["siniestros-v*"]
  require_manual_approval   = var.require_manual_approval
  vpc_id                    = module.network.vpc_id
  private_subnet_ids        = module.network.private_subnet_ids
  cluster_name              = module.eks.cluster_name
  cluster_security_group_id = module.eks.node_security_group_id
  service_namespace         = "siniestros"
  ecr_repository_arn        = module.ecr_siniestros.repository_arn
  ecr_repository_url        = module.ecr_siniestros.repository_url
  tags                      = local.tags
}

output "siniestros_ecr_repository_url" {
  value = module.ecr_siniestros.repository_url
}

output "siniestros_pipeline_name" {
  value = module.cicd_siniestros.pipeline_name
}

# Evidencias confirmadas. El pod solo guarda en disco los fragmentos de cargas en curso;
# el nombre lo repite deploy/k8s/siniestros/deployment.yaml (SINIESTROS_EVIDENCIAS_BUCKET).
module "s3_siniestros_evidencias" {
  source = "../../../modules/s3-private-bucket"

  bucket_name = "${var.name_prefix}-siniestros-evidencias-${var.account_id}"
  kms_key_arn = module.kms.key_arn
  tags        = local.tags
}

module "iam_irsa_siniestros" {
  source = "../../../modules/irsa-service"

  name                 = "${var.name_prefix}-siniestros"
  oidc_provider_arn    = module.eks.oidc_provider_arn
  oidc_issuer_url      = module.eks.oidc_issuer_url
  service_namespace    = "siniestros"
  service_account_name = "siniestros"
  s3_object_arns       = ["${module.s3_siniestros_evidencias.bucket_arn}/evidencias/*"]
  kms_key_arn          = module.kms.key_arn
  tags                 = local.tags
}

output "siniestros_evidence_bucket" {
  value = module.s3_siniestros_evidencias.bucket_name
}

output "siniestros_role_arn" {
  description = "Anotacion eks.amazonaws.com/role-arn del ServiceAccount siniestros."
  value       = module.iam_irsa_siniestros.role_arn
}
