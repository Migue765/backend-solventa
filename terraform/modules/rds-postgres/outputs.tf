output "endpoint" {
  description = "Endpoint (host) de la instancia."
  value       = aws_db_instance.this.address
}

output "port" {
  description = "Puerto."
  value       = aws_db_instance.this.port
}

output "database_name" {
  description = "Nombre de la base de datos."
  value       = aws_db_instance.this.db_name
}

output "master_user_secret_arn" {
  description = "ARN del secreto (Secrets Manager) con las credenciales master."
  value       = aws_db_instance.this.master_user_secret[0].secret_arn
}

output "security_group_id" {
  description = "SG de la base."
  value       = aws_security_group.db.id
}

output "instance_arn" {
  description = "ARN de la instancia."
  value       = aws_db_instance.this.arn
}
