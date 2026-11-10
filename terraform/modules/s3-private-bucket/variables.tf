variable "bucket_name" {
  description = "Nombre global unico del bucket."
  type        = string
}

variable "kms_key_arn" {
  description = "ARN de la CMK para SSE-KMS."
  type        = string
}

variable "noncurrent_version_retention_days" {
  description = "Dias que se conserva una version sobrescrita o borrada antes de eliminarla definitivamente."
  type        = number
  default     = 7

  validation {
    condition     = var.noncurrent_version_retention_days >= 1
    error_message = "La retencion de versiones previas debe ser de al menos 1 dia."
  }
}

variable "tags" {
  description = "Tags comunes."
  type        = map(string)
  default     = {}
}
