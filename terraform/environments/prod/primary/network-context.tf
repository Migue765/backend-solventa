locals {
  network_security_group_ids = merge({
    db       = var.network_context.security_group_ids.db
    redis    = var.network_context.security_group_ids.redis
    alb      = var.network_context.security_group_ids.alb
    vpc_link = var.network_context.security_group_ids.vpc_link
  }, { for service, id in var.network_context.security_group_ids.cicd : "cicd-${service}" => id })
  network_security_group_names = merge({
    db       = "${var.name_prefix}-db"
    redis    = "${var.name_prefix}-redis-sg"
    alb      = "${var.name_prefix}-alb-internal"
    vpc_link = "${var.name_prefix}-vpclink"
    }, {
    for service in keys(var.network_context.security_group_ids.cicd) :
    "cicd-${service}" => "${service == "cotizacion" ? var.name_prefix : "${var.name_prefix}-${service}"}-codebuild-deploy"
  })
}

data "aws_vpc" "network" {
  id = var.network_context.vpc_id

  lifecycle {
    postcondition {
      condition = (
        self.owner_id == var.account_id && self.arn == var.network_context.vpc_arn &&
        try(self.tags["Project"], "") == "solventa" &&
        try(self.tags["Component"], "") == "backend" &&
        try(self.tags["Environment"], "") == var.environment &&
        try(self.tags["Name"], "") == "${var.name_prefix}-vpc"
      )
      error_message = "La VPC debe ser la red backend nueva revisada; no Jenkins/origen ni otra cuenta."
    }
    postcondition {
      condition = (
        length(toset([for id in var.network_context.private_subnet_ids : data.aws_subnet.network[id].availability_zone])) == 2 &&
        toset([for id in var.network_context.private_subnet_ids : data.aws_subnet.network[id].availability_zone]) ==
        toset([for id in var.network_context.public_subnet_ids : data.aws_subnet.network[id].availability_zone])
      )
      error_message = "Las dos parejas de subnets deben conservar las mismas dos AZ de network."
    }
  }
}

data "aws_subnet" "network" {
  for_each = toset(concat(var.network_context.public_subnet_ids, var.network_context.private_subnet_ids))
  id       = each.value

  lifecycle {
    postcondition {
      condition = (
        self.owner_id == var.account_id && self.vpc_id == var.network_context.vpc_id &&
        try(self.tags["Tier"], "") == (contains(var.network_context.private_subnet_ids, each.key) ? "private" : "public")
      )
      error_message = "Cada subnet debe pertenecer a la cuenta/VPC y conservar su Tier publico/privado."
    }
  }
}

data "aws_security_group" "network" {
  for_each = local.network_security_group_ids
  id       = each.value

  lifecycle {
    postcondition {
      condition = (
        self.arn == "arn:aws:ec2:${var.aws_region}:${var.account_id}:security-group/${each.value}" &&
        self.vpc_id == var.network_context.vpc_id &&
        self.name == local.network_security_group_names[each.key]
      )
      error_message = "Cada SG debe corresponder al servicio previsto dentro de la VPC/cuenta nuevas."
    }
  }
}
