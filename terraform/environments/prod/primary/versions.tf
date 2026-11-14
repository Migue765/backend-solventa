terraform {
  required_version = ">= 1.11"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }

  # Completar con -backend-config; no reutilizar el state de otra cuenta.
  backend "s3" {
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region              = var.aws_region
  profile             = var.aws_profile
  allowed_account_ids = [var.account_id]

  default_tags {
    tags = local.tags
  }
}

data "aws_caller_identity" "deployment" {
  lifecycle {
    postcondition {
      condition     = startswith(self.arn, "arn:aws:sts::${var.account_id}:assumed-role/")
      error_message = "Terraform requiere un perfil de rol asumido de la cuenta autorizada; no usar root ni usuarios IAM."
    }
  }
}
