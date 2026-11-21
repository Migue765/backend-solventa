locals {
  api_services = toset([
    "asistencias",
    "bff-movil",
    "bff-web",
    "cotizacion",
    "identidad",
    "notificaciones",
    "perfilamiento",
    "polizas",
    "siniestros",
  ])
  deployment_role_name = element(reverse(split("/", var.deployment_role_arn)), 0)
  tags = {
    Project   = "solventa"
    ManagedBy = "terraform"
    Component = "backend"
  }
}

data "aws_caller_identity" "deployment" {
  lifecycle {
    postcondition {
      condition = (
        self.account_id == var.account_id &&
        startswith(self.arn, "arn:aws:sts::${var.account_id}:assumed-role/${local.deployment_role_name}/")
      )
      error_message = "Usar exclusivamente el rol de despliegue aprobado de 096571798698; no root, usuarios IAM ni otra identidad."
    }
  }
}

module "ecr" {
  for_each = local.api_services
  source   = "../../../modules/ecr"

  repository_name = "solventa/${each.key}"
  force_delete    = false
  tags            = merge(local.tags, { Service = each.key })

  depends_on = [data.aws_caller_identity.deployment]
}

import {
  for_each = var.adopt_created_ecr ? local.api_services : toset([])
  to       = module.ecr[each.value].aws_ecr_repository.this
  id       = "solventa/${each.value}"
}

import {
  for_each = var.adopt_created_ecr ? local.api_services : toset([])
  to       = module.ecr[each.value].aws_ecr_lifecycle_policy.this
  id       = "solventa/${each.value}"
}
