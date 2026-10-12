output "pipeline_name" {
  description = "Nombre del pipeline."
  value       = aws_codepipeline.this.name
}

output "connection_arn" {
  description = "ARN de la conexion con GitHub (autorizarla una vez en la consola)."
  value       = aws_codestarconnections_connection.github.arn
}

output "connection_status" {
  description = "PENDING hasta autorizar la conexion; AVAILABLE despues."
  value       = aws_codestarconnections_connection.github.connection_status
}

output "artifacts_bucket" {
  description = "Bucket de artefactos y logs del pipeline."
  value       = aws_s3_bucket.artifacts.bucket
}

output "deploy_role_arn" {
  description = "Rol de CodeBuild con acceso al namespace del servicio."
  value       = aws_iam_role.deploy.arn
}
