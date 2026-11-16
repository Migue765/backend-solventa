locals {
  tags = {
    Project     = "solventa"
    Environment = var.environment
    ManagedBy   = "terraform"
    Component   = "backend"
  }
  cicd_prefixes = {
    for service in [
      "cotizacion", "bff-web", "bff-movil", "identidad", "perfilamiento",
      "polizas", "siniestros", "notificaciones", "asistencias"
    ] : service => service == "cotizacion" ? var.name_prefix : "${var.name_prefix}-${service}"
  }
}

module "network" {
  source = "../../../modules/network"

  name         = var.name_prefix
  vpc_cidr     = var.vpc_cidr
  az_count     = 2
  cluster_name = "${var.name_prefix}-eks"
  tags         = local.tags
}

# Las reglas pertenecen a primary; no declarar reglas inline aqui.
resource "aws_security_group" "cicd" {
  for_each = local.cicd_prefixes

  name        = "${each.value}-codebuild-deploy"
  description = each.key == "perfilamiento" ? "CodeBuild de despliegue: HTTPS y paquetes HTTP, sin entrada" : "CodeBuild de despliegue: salida libre, sin entrada"
  vpc_id      = module.network.vpc_id
  tags        = merge(local.tags, { Name = "${each.value}-codebuild-deploy" })
}

resource "aws_security_group" "db" {
  name        = "${var.name_prefix}-db"
  description = "PostgreSQL ${var.db_name}: solo desde los nodos EKS"
  vpc_id      = module.network.vpc_id
  tags        = merge(local.tags, { Name = "${var.name_prefix}-db" })
}

resource "aws_security_group" "redis" {
  name        = "${var.name_prefix}-redis-sg"
  description = "Redis ${var.name_prefix}: solo desde los nodos EKS"
  vpc_id      = module.network.vpc_id
  tags        = merge(local.tags, { Name = "${var.name_prefix}-redis-sg" })
}

resource "aws_security_group" "alb" {
  name        = "${var.name_prefix}-alb-internal"
  description = "ALB interno: solo acepta trafico del VPC Link"
  vpc_id      = module.network.vpc_id
  tags        = merge(local.tags, { Name = "${var.name_prefix}-alb-internal" })
}

resource "aws_security_group" "vpc_link" {
  name        = "${var.name_prefix}-vpclink"
  description = "VPC Link de API Gateway hacia el ALB interno"
  vpc_id      = module.network.vpc_id
  tags        = merge(local.tags, { Name = "${var.name_prefix}-vpclink" })
}
