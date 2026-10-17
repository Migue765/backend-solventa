output "endpoint" {
  description = "Endpoint primario (host) de Redis."
  value       = aws_elasticache_replication_group.this.primary_endpoint_address
}

output "port" {
  description = "Puerto."
  value       = 6379
}

output "secret_arn" {
  description = "ARN del secreto con auth_token, host, port y url (rediss://)."
  value       = aws_secretsmanager_secret.auth.arn
}

output "security_group_id" {
  description = "SG de Redis."
  value       = aws_security_group.redis.id
}
