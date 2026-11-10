variable "name" {
  description = "Nombre del rol (ej. solventa-prod-bff-web)."
  type        = string
}

variable "oidc_provider_arn" {
  description = "ARN del OIDC provider del cluster."
  type        = string
}

variable "oidc_issuer_url" {
  description = "Issuer OIDC del cluster (con o sin https://)."
  type        = string
}

variable "service_namespace" {
  description = "Namespace del servicio."
  type        = string
}

variable "service_account_name" {
  description = "ServiceAccount del servicio."
  type        = string
}

variable "secret_arns" {
  description = "Secretos de Secrets Manager que el servicio puede leer."
  type        = list(string)
  default     = []
}

variable "s3_object_arns" {
  description = "ARNs de objetos S3 (bucket/prefijo/*) que el servicio puede leer, escribir y borrar."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for arn in var.s3_object_arns : can(regex("^arn:aws:s3:::[^/]+/.+$", arn))])
    error_message = "Cada ARN debe apuntar a objetos (arn:aws:s3:::bucket/prefijo/*), no al bucket completo."
  }
}

variable "kms_key_arn" {
  description = "CMK que protege los secretos y los objetos S3 del servicio."
  type        = string
}

variable "tags" {
  description = "Tags comunes."
  type        = map(string)
  default     = {}
}
