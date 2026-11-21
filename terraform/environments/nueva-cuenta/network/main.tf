locals {
  tags = {
    Project   = "solventa"
    Component = "backend"
    ManagedBy = "terraform"
  }
  group_names = merge({
    db       = "${var.name_prefix}-db"
    redis    = "${var.name_prefix}-redis-sg"
    alb      = "${var.name_prefix}-alb-internal"
    vpc_link = "${var.name_prefix}-vpclink"
    }, {
    for service in [
      "asistencias", "bff-movil", "bff-web", "cotizacion", "identidad",
      "notificaciones", "perfilamiento", "polizas", "siniestros",
    ] : "cicd-${service}" => "${var.name_prefix}-${service}-codebuild-deploy"
  })
}

data "aws_caller_identity" "deployment" {
  lifecycle {
    postcondition {
      condition = self.account_id == "096571798698" && startswith(
        self.arn, "arn:aws:sts::096571798698:assumed-role/${element(reverse(split("/", var.deployment_role_arn)), 0)}/"
      )
      error_message = "Network requiere el rol asumido aprobado de la cuenta nueva."
    }
  }
}

module "network" {
  source       = "../../../modules/network"
  name         = var.name_prefix
  vpc_cidr     = "10.20.0.0/16"
  az_count     = 2
  cluster_name = "${var.name_prefix}-eks"
  tags         = local.tags

  depends_on = [data.aws_caller_identity.deployment]
}

resource "aws_security_group" "groups" {
  for_each    = local.group_names
  name        = each.value
  description = "Backend cuenta nueva: ${each.key}; reglas administradas por primary"
  vpc_id      = module.network.vpc_id
  tags        = merge(local.tags, { Name = each.value })
}

output "network_context" {
  description = "Exportar solo este output y revisar metadatos antes de primary."
  value = {
    account_id         = "096571798698"
    region             = "us-east-2"
    name_prefix        = var.name_prefix
    vpc_id             = module.network.vpc_id
    vpc_arn            = "arn:aws:ec2:us-east-2:096571798698:vpc/${module.network.vpc_id}"
    public_subnet_ids  = module.network.public_subnet_ids
    private_subnet_ids = module.network.private_subnet_ids
    security_group_ids = {
      db       = aws_security_group.groups["db"].id
      redis    = aws_security_group.groups["redis"].id
      alb      = aws_security_group.groups["alb"].id
      vpc_link = aws_security_group.groups["vpc_link"].id
      cicd = {
        for key, group in aws_security_group.groups :
        trimprefix(key, "cicd-") => group.id if startswith(key, "cicd-")
      }
    }
  }
}
