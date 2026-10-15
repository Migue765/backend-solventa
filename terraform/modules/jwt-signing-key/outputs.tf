output "secret_arn" {
  description = "ARN del secreto con la clave privada PEM (JWT_PRIVATE_KEY de identidad)."
  value       = aws_secretsmanager_secret.this.arn
}

output "public_key_pem" {
  description = "Clave publica PEM (informativa; identidad la publica en su JWKS)."
  value       = tls_private_key.this.public_key_pem
}
