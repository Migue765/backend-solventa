output "endpoint" {
  description = "Endpoint writer del cluster."
  value       = aws_rds_cluster.this.endpoint
}

output "reader_endpoint" {
  description = "Endpoint reader del cluster."
  value       = aws_rds_cluster.this.reader_endpoint
}

output "port" {
  description = "Puerto."
  value       = aws_rds_cluster.this.port
}

output "database_name" {
  description = "Nombre de la base de datos."
  value       = aws_rds_cluster.this.database_name
}

output "master_user_secret_arn" {
  description = "ARN del secreto (Secrets Manager) con las credenciales master."
  value       = aws_rds_cluster.this.master_user_secret[0].secret_arn
}

output "security_group_id" {
  description = "SG del cluster."
  value       = aws_security_group.db.id
}

output "cluster_arn" {
  description = "ARN del cluster."
  value       = aws_rds_cluster.this.arn
}
