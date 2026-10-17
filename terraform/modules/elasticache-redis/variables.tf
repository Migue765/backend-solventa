variable "name" {
  description = "Prefijo de nombres (ej. solventa-prod)."
  type        = string
}

variable "vpc_id" {
  description = "VPC donde vive Redis."
  type        = string
}

variable "subnet_ids" {
  description = "Subnets privadas para el subnet group."
  type        = list(string)
}

variable "allowed_security_group_ids" {
  description = "SGs que pueden conectarse al puerto 6379 (nodos EKS)."
  type        = list(string)
}

variable "kms_key_arn" {
  description = "CMK para cifrado en reposo y para el secreto del auth token."
  type        = string
}

variable "node_type" {
  description = "Tipo de nodo."
  type        = string
  default     = "cache.t4g.micro"
}

variable "engine_version" {
  description = "Version de Redis."
  type        = string
  default     = "7.1"
}

variable "parameter_group_name" {
  description = "Parameter group (debe corresponder a la familia de engine_version)."
  type        = string
  default     = "default.redis7"
}

variable "snapshot_retention_limit" {
  description = "Dias de snapshots automaticos (0 los desactiva; las sesiones son efimeras)."
  type        = number
  default     = 0
}

variable "secret_recovery_window_in_days" {
  description = "Ventana de recuperacion del secreto al destruirlo (0 = borrado inmediato)."
  type        = number
  default     = 7
}

variable "tags" {
  description = "Tags comunes."
  type        = map(string)
  default     = {}
}
