locals {
  identifier        = coalesce(var.identifier, "${var.name}-${var.database_name}")
  security_group_id = var.existing_security_group_id != null ? var.existing_security_group_id : one(aws_security_group.db[*].id)
}

resource "aws_db_subnet_group" "this" {
  name       = local.identifier
  subnet_ids = var.subnet_ids

  tags = merge(var.tags, { Name = local.identifier })
}

resource "aws_security_group" "db" {
  count = var.existing_security_group_id == null ? 1 : 0

  name        = "${local.identifier}-db"
  description = "PostgreSQL ${var.database_name}: solo desde los nodos EKS"
  vpc_id      = var.vpc_id

  tags = merge(var.tags, { Name = "${local.identifier}-db" })
}

moved {
  from = aws_security_group.db
  to   = aws_security_group.db[0]
}

resource "aws_vpc_security_group_ingress_rule" "from_eks" {
  count = length(var.allowed_security_group_ids)

  security_group_id            = local.security_group_id
  referenced_security_group_id = var.allowed_security_group_ids[count.index]
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  description                  = "PostgreSQL desde SG de nodos EKS"
}

resource "aws_db_instance" "this" {
  identifier     = local.identifier
  engine         = "postgres"
  engine_version = var.engine_version
  instance_class = var.instance_class

  allocated_storage = var.allocated_storage
  storage_type      = "gp2"
  storage_encrypted = true
  kms_key_id        = var.kms_key_arn

  db_name                             = var.database_name
  port                                = 5432
  username                            = var.master_username
  manage_master_user_password         = true
  master_user_secret_kms_key_id       = var.kms_key_arn
  iam_database_authentication_enabled = true

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [local.security_group_id]
  publicly_accessible    = false
  multi_az               = var.multi_az

  backup_retention_period      = var.backup_retention_period
  backup_window                = "06:00-07:00"
  maintenance_window           = "sun:07:30-sun:08:30"
  copy_tags_to_snapshot        = true
  auto_minor_version_upgrade   = true
  performance_insights_enabled = false
  apply_immediately            = true

  deletion_protection       = var.deletion_protection
  skip_final_snapshot       = var.skip_final_snapshot
  final_snapshot_identifier = var.skip_final_snapshot ? null : "${local.identifier}-final"

  tags = merge(var.tags, { Name = local.identifier })
}
