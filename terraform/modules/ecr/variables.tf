variable "repository_name" {
  description = "Nombre del repositorio ECR."
  type        = string
  default     = "solventa/cotizacion"
}

variable "keep_last_images" {
  description = "Cantidad de imagenes a conservar (lifecycle)."
  type        = number
  default     = 10
}

variable "force_delete" {
  description = "Permite borrar el repositorio aunque tenga imagenes (solo mientras no haya trafico real)."
  type        = bool
  default     = false
}

variable "tags" {
  description = "Tags comunes."
  type        = map(string)
  default     = {}
}
