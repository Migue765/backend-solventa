variable "bucket_name" {
  description = "Nombre global unico del bucket de evidencias."
  type        = string
}

variable "kms_key_arn" {
  description = "ARN de la CMK para SSE-KMS."
  type        = string
}

variable "object_lock_mode" {
  description = "Modo de retencion por defecto de Object Lock (GOVERNANCE o COMPLIANCE)."
  type        = string
  default     = "GOVERNANCE"

  validation {
    condition     = contains(["GOVERNANCE", "COMPLIANCE"], var.object_lock_mode)
    error_message = "object_lock_mode debe ser GOVERNANCE o COMPLIANCE."
  }
}

variable "object_lock_retention_days" {
  description = "Dias de retencion por defecto (prueba: 1)."
  type        = number
  default     = 1

  validation {
    condition     = var.object_lock_retention_days >= 1
    error_message = "La retencion debe ser de al menos 1 dia."
  }
}

variable "tags" {
  description = "Tags comunes."
  type        = map(string)
  default     = {}
}
