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

output "bff_web_ecr_repository_url" {
  description = "URL del repositorio ECR de bff-web."
  value       = module.ecr_bff_web.repository_url
}

output "redis_endpoint" {
  description = "Endpoint de Redis (limitador de intentos de identidad)."
  value       = module.redis.endpoint
}

output "redis_secret_arn" {
  description = "Secreto con auth_token y url (rediss://) de Redis."
  value       = module.redis.secret_arn
}

output "bff_web_target_group_arn" {
  description = "ARN del target group de bff-web (TargetGroupBinding)."
  value       = module.api_gateway.bff_web_target_group_arn
}

output "bff_web_pipeline_name" {
  description = "Pipeline de despliegue continuo de bff-web (tag bff-web-v*)."
  value       = module.cicd_bff_web.pipeline_name
}

output "bff_movil_ecr_repository_url" {
  description = "URL del repositorio ECR de bff-movil."
  value       = module.ecr_bff_movil.repository_url
}

output "bff_movil_target_group_arn" {
  description = "ARN del target group de bff-movil (TargetGroupBinding)."
  value       = module.api_gateway.bff_movil_target_group_arn
}

output "bff_movil_pipeline_name" {
  description = "Pipeline de despliegue continuo de bff-movil (tag bff-movil-v*)."
  value       = module.cicd_bff_movil.pipeline_name
}

output "movil_api_url" {
  description = "URL base del API para la app nativa (rutas /movil/v1/...)."
  value       = module.api_gateway.api_endpoint
}

output "identidad_ecr_repository_url" {
  description = "URL del repositorio ECR de identidad."
  value       = module.ecr_identidad.repository_url
}

output "identidad_role_arn" {
  description = "Rol IRSA de identidad (anotacion del ServiceAccount)."
  value       = module.iam_irsa_identidad.role_arn
}

output "identidad_target_group_arn" {
  description = "ARN del target group de identidad (TargetGroupBinding)."
  value       = module.api_gateway.identidad_target_group_arn
}

output "identidad_pipeline_name" {
  description = "Pipeline de despliegue continuo de identidad (tag identidad-v*)."
  value       = module.cicd_identidad.pipeline_name
}

output "jwt_key_secret_arn" {
  description = "Secreto con la clave privada PEM con la que identidad firma los JWT (JWT_PRIVATE_KEY)."
  value       = module.jwt_key.secret_arn
}

output "jwt_issuer" {
  description = "Emisor (iss) de los JWT: URL base del API, sin barra final (JWT_ISSUER)."
  value       = module.api_gateway.issuer
}

output "jwt_audience" {
  description = "Audiencia (aud) de los JWT (JWT_AUDIENCE)."
  value       = module.api_gateway.jwt_audience
}
