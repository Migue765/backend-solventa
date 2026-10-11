variable "name" {
  description = "Prefijo de nombres (ej. solventa-prod)."
  type        = string
}

variable "vpc_cidr" {
  description = "CIDR de la VPC."
  type        = string
  default     = "10.20.0.0/16"
}

variable "az_count" {
  description = "Numero de zonas de disponibilidad."
  type        = number
  default     = 2
}

variable "cluster_name" {
  description = "Nombre del cluster EKS (para los tags kubernetes.io/cluster/*). Null para omitir."
  type        = string
  default     = null
}

variable "tags" {
  description = "Tags comunes."
  type        = map(string)
  default     = {}
}
