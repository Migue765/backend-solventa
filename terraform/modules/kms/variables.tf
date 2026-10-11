variable "name" {
  description = "Prefijo de nombres (ej. solventa-dev)."
  type        = string
}

variable "alias_suffix" {
  description = "Sufijo del alias: alias/<name>-<alias_suffix>."
  type        = string
  default     = "data"
}

variable "deletion_window_in_days" {
  description = "Ventana de espera para borrar la llave (7-30)."
  type        = number
  default     = 7
}

variable "service_principals" {
  description = "Servicios AWS que usan la llave directamente (no via IAM del llamador), ej. SNS entregando a SQS cifrada."
  type        = list(string)
  default     = ["sns.amazonaws.com"]
}

variable "tags" {
  description = "Tags comunes."
  type        = map(string)
  default     = {}
}
