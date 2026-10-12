output "ecr_repository_url" {
  description = "URL del repositorio ECR."
  value       = module.ecr.repository_url
}

output "cluster_name" {
  description = "Nombre del cluster EKS."
  value       = module.eks.cluster_name
}

output "db_endpoint" {
  description = "Endpoint de la base PostgreSQL."
  value       = module.database.endpoint
}

output "db_secret_arn" {
  description = "ARN del secreto con credenciales master de la base."
  value       = module.database.master_user_secret_arn
}

output "api_endpoint" {
  description = "URL del HTTP API."
  value       = module.api_gateway.api_endpoint
}

output "evidence_bucket" {
  description = "Bucket de evidencias (Object Lock)."
  value       = module.s3_evidence.bucket_name
}

output "sns_topic_arn" {
  description = "ARN del topic instantanea-decision-registrada."
  value       = module.messaging.topic_arn
}

output "sqs_queue_url" {
  description = "URL de la cola suscrita al topic."
  value       = module.messaging.queue_url
}

output "service_role_arn" {
  description = "Rol IRSA del servicio (anotacion del ServiceAccount)."
  value       = module.iam_irsa.service_role_arn
}

output "target_group_arn" {
  description = "ARN del target group (TargetGroupBinding)."
  value       = module.api_gateway.target_group_arn
}

output "kubeconfig_command" {
  description = "Comando para configurar kubectl."
  value       = "aws eks update-kubeconfig --name ${module.eks.cluster_name} --region ${var.aws_region} --profile ${var.aws_profile}"
}

output "pipeline_name" {
  description = "Pipeline de despliegue continuo."
  value       = module.cicd.pipeline_name
}

output "github_connection_arn" {
  description = "Conexion con GitHub: autorizarla una vez en la consola (estado PENDING hasta entonces)."
  value       = module.cicd.connection_arn
}
