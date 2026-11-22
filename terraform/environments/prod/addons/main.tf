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
  }

  backend "s3" {
    encrypt      = true
    use_lockfile = true
  }
}

variable "cluster_addons" {
  description = "Output no secreto cluster_addons del primary de destino ya aplicado."
  type = object({
    account_id                   = string
    aws_region                   = string
    aws_profile                  = string
    cluster_name                 = string
    vpc_id                       = string
    alb_controller_role_arn      = string
    alb_controller_chart_version = string
    metrics_server_chart_version = string
  })

  validation {
    condition = var.cluster_addons.aws_region == "us-east-2" && can(regex("^[0-9]{12}$", var.cluster_addons.account_id)) && (
      length(trimspace(var.cluster_addons.aws_profile)) > 0
      ) && can(regex(
        "^arn:aws:iam::${var.cluster_addons.account_id}:role/.+$",
        var.cluster_addons.alb_controller_role_arn
    ))
    error_message = "Indicar cuenta, perfil y rol IRSA reales del cluster de destino."
  }
}

provider "aws" {
  region              = var.cluster_addons.aws_region
  profile             = var.cluster_addons.aws_profile
  allowed_account_ids = [var.cluster_addons.account_id]
}

data "aws_caller_identity" "deployment" {
  lifecycle {
    postcondition {
      condition     = startswith(self.arn, "arn:aws:sts::${var.cluster_addons.account_id}:assumed-role/")
      error_message = "Addons requiere un rol asumido de la cuenta autorizada; no usar root ni usuarios IAM."
    }
  }
}

data "aws_eks_cluster" "this" {
  name = var.cluster_addons.cluster_name

  lifecycle {
    postcondition {
      condition     = self.vpc_config[0].vpc_id == var.cluster_addons.vpc_id
      error_message = "El cluster no pertenece a la VPC de backend aprobada."
    }
  }
}

provider "helm" {
  kubernetes {
    host                   = data.aws_eks_cluster.this.endpoint
    cluster_ca_certificate = base64decode(data.aws_eks_cluster.this.certificate_authority[0].data)

    exec {
      api_version = "client.authentication.k8s.io/v1beta1"
      command     = "aws"
      args = [
        "eks", "get-token",
        "--cluster-name", var.cluster_addons.cluster_name,
        "--region", var.cluster_addons.aws_region,
        "--profile", var.cluster_addons.aws_profile,
      ]
    }
  }
}

resource "helm_release" "aws_load_balancer_controller" {
  name       = "aws-load-balancer-controller"
  namespace  = "kube-system"
  repository = "https://aws.github.io/eks-charts"
  chart      = "aws-load-balancer-controller"
  version    = var.cluster_addons.alb_controller_chart_version

  set {
    name  = "clusterName"
    value = var.cluster_addons.cluster_name
  }
  set {
    name  = "region"
    value = var.cluster_addons.aws_region
  }
  set {
    name  = "vpcId"
    value = var.cluster_addons.vpc_id
  }
  set {
    name  = "serviceAccount.name"
    value = "aws-load-balancer-controller"
  }
  set {
    name  = "serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn"
    value = var.cluster_addons.alb_controller_role_arn
  }
}

resource "helm_release" "metrics_server" {
  name       = "metrics-server"
  namespace  = "kube-system"
  repository = "https://kubernetes-sigs.github.io/metrics-server/"
  chart      = "metrics-server"
  version    = var.cluster_addons.metrics_server_chart_version
}
