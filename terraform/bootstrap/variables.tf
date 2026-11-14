variable "aws_region" {
  description = "Region AWS."
  type        = string
  default     = "us-east-2"
  validation {
    condition     = var.aws_region == "us-east-2"
    error_message = "El bootstrap de esta reconstruccion corresponde a us-east-2."
  }
}

variable "aws_profile" {
  description = "Perfil AWS CLI de rol asumido para bootstrap, nunca root."
  type        = string
  validation {
    condition     = length(trimspace(var.aws_profile)) > 0
    error_message = "Indicar el perfil real del rol de bootstrap."
  }
}

variable "account_id" {
  description = "Cuenta AWS autorizada para crear el state nuevo."
  type        = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "Indicar un ID de cuenta AWS de 12 digitos."
  }
}

variable "state_bucket_name" {
  description = "Nombre del bucket de estado. Si es null se usa solventa-tfstate-<account_id>."
  type        = string
  default     = null
}

variable "tags" {
  description = "Tags comunes."
  type        = map(string)
  default = {
    Project     = "solventa"
    Environment = "shared"
    ManagedBy   = "terraform"
  }
}
