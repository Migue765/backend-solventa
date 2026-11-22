locals {
  network_security_group_ids = merge({
    db       = var.network_context.security_group_ids.db
    redis    = var.network_context.security_group_ids.redis
    alb      = var.network_context.security_group_ids.alb
    vpc_link = var.network_context.security_group_ids.vpc_link
  }, { for service, id in var.network_context.security_group_ids.cicd : "cicd-${service}" => id })
}

data "aws_vpc" "network" {
  id = var.network_context.vpc_id
  lifecycle {
    postcondition {
      condition = (
        self.owner_id == var.account_id && !self.default &&
        self.arn == var.network_context.vpc_arn &&
        try(self.tags["Name"], "") == "${var.name_prefix}-vpc" &&
        try(self.tags["Component"], "") == "backend" &&
        length(toset([for id in var.network_context.private_subnet_ids : data.aws_subnet.network[id].availability_zone])) == 2 &&
        toset([for id in var.network_context.private_subnet_ids : data.aws_subnet.network[id].availability_zone]) ==
        toset([for id in var.network_context.public_subnet_ids : data.aws_subnet.network[id].availability_zone])
      )
      error_message = "La VPC debe ser nueva de backend, no default, de esta cuenta y con dos pares de AZ."
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
      error_message = "Subnet ajena a la cuenta/VPC o con Tier incorrecto."
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
        self.name == (
          startswith(each.key, "cicd-") ?
          "${var.name_prefix}-${trimprefix(each.key, "cicd-")}-codebuild-deploy" :
          lookup({
            db       = "${var.name_prefix}-db"
            redis    = "${var.name_prefix}-redis-sg"
            alb      = "${var.name_prefix}-alb-internal"
            vpc_link = "${var.name_prefix}-vpclink"
          }, each.key, "")
        )
      )
      error_message = "SG ajeno a la cuenta/VPC o asignado a otro servicio."
    }
  }
}
