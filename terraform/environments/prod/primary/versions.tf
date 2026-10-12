terraform {
  required_version = ">= 1.11"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 2.17"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }

  # El bucket lo crea terraform/bootstrap (solventa-tfstate-<account_id>).
  # Los backends no admiten variables: ajustar bucket/profile si cambian.
  backend "s3" {
    bucket       = "solventa-tfstate-976083331509"
    key          = "environments/prod/primary/terraform.tfstate"
    region       = "us-east-2"
    profile      = "tesis"
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

provider "helm" {
  kubernetes {
    host                   = module.eks.cluster_endpoint
    cluster_ca_certificate = base64decode(module.eks.cluster_certificate_authority_data)

    exec {
      api_version = "client.authentication.k8s.io/v1beta1"
      command     = "aws"
      args = [
        "eks", "get-token",
        "--cluster-name", module.eks.cluster_name,
        "--region", var.aws_region,
        "--profile", var.aws_profile,
      ]
    }
  }
}
