variable "account_id" {
  type        = string
  description = "Cuenta de esta reconstruccion; sin default para evitar una seleccion implicita."
  validation {
    condition     = var.account_id == "396383602233"
    error_message = "La red nueva solo corresponde a 396383602233."
  }
}

variable "aws_region" {
  type    = string
  default = "us-east-2"
  validation {
    condition     = var.aws_region == "us-east-2"
    error_message = "La red nueva solo corresponde a us-east-2."
  }
}

variable "aws_profile" {
  type        = string
  description = "Perfil real de rol asumido, nunca root."
  validation {
    condition     = length(trimspace(var.aws_profile)) > 0
    error_message = "Indicar el perfil real del rol de despliegue de red."
  }
}

variable "environment" {
  type    = string
  default = "prod"
}

variable "name_prefix" {
  type    = string
  default = "solventa-prod"
}

variable "vpc_cidr" {
  type    = string
  default = "10.20.0.0/16"
}

variable "db_name" {
  type        = string
  description = "Nombre logico previsto, solo para conservar la descripcion del SG."
  default     = "solventa_prod"
}
