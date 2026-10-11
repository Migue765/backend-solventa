variable "name" {
  description = "Prefijo de nombres (ej. solventa-dev)."
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

variable "alb_controller_namespace" {
  description = "Namespace del AWS Load Balancer Controller."
  type        = string
  default     = "kube-system"
}

variable "alb_controller_service_account" {
  description = "ServiceAccount del AWS Load Balancer Controller."
  type        = string
  default     = "aws-load-balancer-controller"
}

variable "service_namespace" {
  description = "Namespace del servicio cotizacion."
  type        = string
  default     = "cotizacion"
}

variable "service_account_name" {
  description = "ServiceAccount del servicio cotizacion."
  type        = string
  default     = "cotizacion"
}

variable "db_secret_arn" {
  description = "ARN del secreto de la base de datos."
  type        = string
}

variable "evidence_bucket_arn" {
  description = "ARN del bucket de evidencias."
  type        = string
}

variable "sns_topic_arn" {
  description = "ARN del topic SNS donde el servicio publica."
  type        = string
}

variable "kms_key_arn" {
  description = "ARN de la CMK que protege secreto, bucket y topic."
  type        = string
}

variable "tags" {
  description = "Tags comunes."
  type        = map(string)
  default     = {}
}
