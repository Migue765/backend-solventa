terraform {
  required_version = ">= 1.11"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

# Bootstrap usa estado LOCAL (no hay backend remoto todavia).
provider "aws" {
  region              = var.aws_region
  profile             = var.aws_profile
  allowed_account_ids = [var.account_id]

  default_tags {
    tags = var.tags
  }
}
