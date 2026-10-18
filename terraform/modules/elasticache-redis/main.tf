locals {
  id = "${var.name}-redis"
}

resource "aws_elasticache_subnet_group" "this" {
  name       = local.id
  subnet_ids = var.subnet_ids

  tags = merge(var.tags, { Name = local.id })
}

resource "aws_security_group" "redis" {
  name        = "${local.id}-sg"
  description = "Redis ${var.name}: solo desde los nodos EKS"
  vpc_id      = var.vpc_id

  tags = merge(var.tags, { Name = "${local.id}-sg" })
}

resource "aws_vpc_security_group_ingress_rule" "from_eks" {
  count = length(var.allowed_security_group_ids)

  security_group_id            = aws_security_group.redis.id
  referenced_security_group_id = var.allowed_security_group_ids[count.index]
  ip_protocol                  = "tcp"
  from_port                    = 6379
  to_port                      = 6379
  description                  = "Redis desde SG de nodos EKS"
}

# El auth token de ElastiCache admite 16-128 caracteres imprimibles sin @, " ni /.
resource "random_password" "auth_token" {
  length  = 48
  special = false
}

resource "aws_elasticache_replication_group" "this" {
  replication_group_id = local.id
  description          = "Sesiones y rate limit de ${var.name}"

  engine               = "redis"
  engine_version       = var.engine_version
  node_type            = var.node_type
  port                 = 6379
  parameter_group_name = var.parameter_group_name

  # Un solo nodo y sin failover: las sesiones son efimeras y el costo importa.
  num_cache_clusters         = 1
  automatic_failover_enabled = false
  multi_az_enabled           = false

  subnet_group_name  = aws_elasticache_subnet_group.this.name
  security_group_ids = [aws_security_group.redis.id]

  at_rest_encryption_enabled = true
  kms_key_id                 = var.kms_key_arn
  transit_encryption_enabled = true
  auth_token                 = random_password.auth_token.result

  snapshot_retention_limit   = var.snapshot_retention_limit
  auto_minor_version_upgrade = true
  apply_immediately          = true

  tags = merge(var.tags, { Name = local.id })
}

resource "aws_secretsmanager_secret" "auth" {
  name                    = "${local.id}-auth"
  description             = "Auth token y URL de conexion de Redis ${local.id}"
  kms_key_id              = var.kms_key_arn
  recovery_window_in_days = var.secret_recovery_window_in_days

  tags = var.tags
}

resource "aws_secretsmanager_secret_version" "auth" {
  secret_id = aws_secretsmanager_secret.auth.id

  secret_string = jsonencode({
    auth_token = random_password.auth_token.result
    host       = aws_elasticache_replication_group.this.primary_endpoint_address
    port       = 6379
    url        = "rediss://:${random_password.auth_token.result}@${aws_elasticache_replication_group.this.primary_endpoint_address}:6379/0"
  })
}
