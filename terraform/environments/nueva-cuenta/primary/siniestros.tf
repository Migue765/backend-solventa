variable "enable_siniestros_evidence" {
  description = "Preparar almacenamiento privado y rol propio; requiere plan y aprobacion antes de aplicar."
  type        = bool
  default     = false
  nullable    = false
}

module "siniestros_evidence" {
  count  = var.enable_siniestros_evidence ? 1 : 0
  source = "../../../modules/s3-private-bucket"

  bucket_name = "${var.name_prefix}-siniestros-evidencias-${var.account_id}"
  kms_key_arn = module.kms.key_arn
  tags        = merge(local.tags, { Service = "siniestros" })
}

module "siniestros_irsa" {
  count  = var.enable_siniestros_evidence ? 1 : 0
  source = "../../../modules/irsa-service"

  permissions_boundary_arn = var.permissions_boundary_arns.application
  name                     = "${var.name_prefix}-siniestros"
  oidc_provider_arn        = module.eks.oidc_provider_arn
  oidc_issuer_url          = module.eks.oidc_issuer_url
  service_namespace        = "siniestros"
  service_account_name     = "siniestros"
  s3_object_arns           = ["${module.siniestros_evidence[0].bucket_arn}/evidencias/*"]
  kms_key_arn              = module.kms.key_arn
  tags                     = merge(local.tags, { Service = "siniestros" })
}

output "siniestros_context" {
  description = "Metadatos para el runtime, sin credenciales; null mientras no se apruebe el almacenamiento."
  value = var.enable_siniestros_evidence ? {
    bucket_name = module.siniestros_evidence[0].bucket_name
    role_arn    = module.siniestros_irsa[0].role_arn
  } : null
}
