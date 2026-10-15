variable "secret_name" {
  description = "Nombre del secreto (ej. solventa-prod-identidad-jwt)."
  type        = string
}

variable "kms_key_arn" {
  description = "CMK que cifra el secreto."
  type        = string
}

variable "rsa_bits" {
  description = "Tamano de la clave RSA."
  type        = number
  default     = 2048
}

variable "secret_recovery_window_in_days" {
  description = "Dias de recuperacion al borrar el secreto (0 lo borra de inmediato)."
  type        = number
  default     = 7
}

variable "tags" {
  description = "Tags comunes."
  type        = map(string)
  default     = {}
}
