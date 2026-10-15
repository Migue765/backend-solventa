locals {
  identifier = "${var.name}-${var.database_name}"
}

resource "aws_db_subnet_group" "this" {
  name       = local.identifier
  subnet_ids = var.subnet_ids

  tags = merge(var.tags, { Name = local.identifier })
}

resource "aws_security_group" "db" {
  name        = "${local.identifier}-db"
  description = "Aurora ${var.database_name}: PostgreSQL solo desde los nodos EKS"
  vpc_id      = var.vpc_id

  tags = merge(var.tags, { Name = "${local.identifier}-db" })
}

resource "aws_vpc_security_group_ingress_rule" "from_eks" {
  count = length(var.allowed_security_group_ids)

  security_group_id            = aws_security_group.db.id
  referenced_security_group_id = var.allowed_security_group_ids[count.index]
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  description                  = "PostgreSQL desde SG de nodos EKS"
}

resource "aws_rds_cluster" "this" {
  cluster_identifier = local.identifier
  engine             = "aurora-postgresql"
  engine_mode        = "provisioned"
  engine_version     = var.engine_version
  database_name      = var.database_name
  port               = 5432

  master_username                     = var.master_username
  manage_master_user_password         = true
  master_user_secret_kms_key_id       = var.kms_key_arn
  iam_database_authentication_enabled = true

  storage_encrypted = true
  kms_key_id        = var.kms_key_arn

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [aws_security_group.db.id]

  backup_retention_period      = var.backup_retention_period
  preferred_backup_window      = "06:00-07:00"
  preferred_maintenance_window = "sun:07:30-sun:08:30"
  copy_tags_to_snapshot        = true

  deletion_protection       = var.deletion_protection
  skip_final_snapshot       = var.skip_final_snapshot
  final_snapshot_identifier = var.skip_final_snapshot ? null : "${local.identifier}-final"

  serverlessv2_scaling_configuration {
    min_capacity = var.min_capacity
    max_capacity = var.max_capacity
  }

  tags = merge(var.tags, { Name = local.identifier })
}

resource "aws_rds_cluster_instance" "this" {
  identifier         = "${local.identifier}-1"
  cluster_identifier = aws_rds_cluster.this.id
  engine             = aws_rds_cluster.this.engine
  engine_version     = aws_rds_cluster.this.engine_version
  instance_class     = "db.serverless"

  db_subnet_group_name = aws_db_subnet_group.this.name
  publicly_accessible  = false

  tags = merge(var.tags, { Name = "${local.identifier}-1" })
}
