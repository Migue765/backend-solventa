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
  description = "Solo el output nombrado del primary nuevo, revisado por el operador."
  nullable    = false
  type = object({
    account_id              = string
    aws_region              = string
    aws_profile             = string
    deployment_role_arn     = string
    cluster_name            = string
    vpc_id                  = string
    alb_controller_role_arn = string
  })
  validation {
    condition = (
      var.cluster_addons.account_id == "096571798698" &&
      var.cluster_addons.aws_region == "us-east-2" &&
      length(trimspace(var.cluster_addons.aws_profile)) > 0 &&
      can(regex("^arn:aws:iam::096571798698:role/([A-Za-z0-9+=,.@_-]+/)*[A-Za-z0-9+=,.@_-]+$", var.cluster_addons.deployment_role_arn)) &&
      can(regex("^arn:aws:iam::096571798698:role/[A-Za-z0-9+=,.@_/-]+$", var.cluster_addons.alb_controller_role_arn))
    )
    error_message = "Addons requiere cuenta/region/perfil y roles aprobados de la cuenta nueva."
  }
}

provider "aws" {
  region              = "us-east-2"
  profile             = var.cluster_addons.aws_profile
  allowed_account_ids = ["096571798698"]
}

data "aws_caller_identity" "deployment" {
  lifecycle {
    postcondition {
      condition = self.account_id == "096571798698" && startswith(
        self.arn, "arn:aws:sts::096571798698:assumed-role/${element(reverse(split("/", var.cluster_addons.deployment_role_arn)), 0)}/"
      )
      error_message = "Addons requiere el rol asumido aprobado de la cuenta nueva."
    }
  }
}

data "aws_eks_cluster" "this" {
  name = var.cluster_addons.cluster_name
  lifecycle {
    postcondition {
      condition = (
        startswith(self.arn, "arn:aws:eks:us-east-2:096571798698:cluster/") &&
        self.vpc_config[0].vpc_id == var.cluster_addons.vpc_id
      )
      error_message = "El cluster debe pertenecer a la cuenta/region/VPC revisadas."
    }
  }
  depends_on = [data.aws_caller_identity.deployment]
}

provider "helm" {
  kubernetes {
    host                   = data.aws_eks_cluster.this.endpoint
    cluster_ca_certificate = base64decode(data.aws_eks_cluster.this.certificate_authority[0].data)
    exec {
      api_version = "client.authentication.k8s.io/v1beta1"
      command     = "aws"
      args = [
        "eks", "get-token", "--cluster-name", var.cluster_addons.cluster_name,
        "--region", "us-east-2", "--profile", var.cluster_addons.aws_profile,
      ]
    }
  }
}

resource "helm_release" "aws_load_balancer_controller" {
  name       = "aws-load-balancer-controller"
  namespace  = "kube-system"
  repository = "https://aws.github.io/eks-charts"
  chart      = "aws-load-balancer-controller"
  version    = "3.6.0"
  set {
    name  = "clusterName"
    value = var.cluster_addons.cluster_name
  }
  set {
    name  = "region"
    value = "us-east-2"
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
  version    = "3.14.0"
}
