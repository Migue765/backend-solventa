variable "aws_region" {
  description = "Region AWS."
  type        = string
  default     = "us-east-2"
}

variable "aws_profile" {
  description = "Perfil de AWS CLI."
  type        = string
  default     = "tesis"
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
