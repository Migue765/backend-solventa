# Par de claves RSA con el que el servicio identidad firma los JWT (RS256).
# La clave privada queda en el state de Terraform (bucket S3 cifrado) y en un secreto de Secrets Manager
# cifrado con la CMK. La publica la sirve identidad en /.well-known/jwks.json.
#
# Rotacion: `terraform apply -replace=module.jwt_key.tls_private_key.this`, recrear el Secret de Kubernetes
# `identidad-config` (JWT_PRIVATE_KEY) y reiniciar identidad. Los access tokens firmados con la clave anterior
# dejan de validar (duran como maximo ACCESS_TOKEN_MINUTES); no hay periodo de solapamiento de claves.
resource "tls_private_key" "this" {
  algorithm = "RSA"
  rsa_bits  = var.rsa_bits
}

resource "aws_secretsmanager_secret" "this" {
  name                    = var.secret_name
  description             = "Clave privada RSA (PEM) con la que identidad firma los JWT"
  kms_key_id              = var.kms_key_arn
  recovery_window_in_days = var.secret_recovery_window_in_days

  tags = var.tags
}

resource "aws_secretsmanager_secret_version" "this" {
  secret_id     = aws_secretsmanager_secret.this.id
  secret_string = tls_private_key.this.private_key_pem
}
