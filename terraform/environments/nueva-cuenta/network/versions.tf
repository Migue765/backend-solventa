terraform {
  required_version = ">= 1.11"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
  backend "s3" {
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region              = "us-east-2"
  profile             = var.aws_profile
  allowed_account_ids = ["096571798698"]
}

variable "aws_profile" {
  type = string
  validation {
    condition     = length(trimspace(var.aws_profile)) > 0
    error_message = "Indicar el perfil real del rol de despliegue."
  }
}

variable "deployment_role_arn" {
  type = string
  validation {
    condition     = can(regex("^arn:aws:iam::096571798698:role/([A-Za-z0-9+=,.@_-]+/)*[A-Za-z0-9+=,.@_-]+$", var.deployment_role_arn))
    error_message = "Indicar el ARN IAM estable del rol aprobado de la cuenta nueva."
  }
}

variable "name_prefix" {
  type    = string
  default = "solventa-nueva"
}
