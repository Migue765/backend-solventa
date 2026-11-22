output "ecr_repositories" {
  description = "Repositorio privado de cada API para sus builds, Deployments y Jobs."
  value = {
    for service, repository in module.ecr : service => {
      name = repository.repository_name
      arn  = repository.repository_arn
      url  = repository.repository_url
    }
  }
}

output "cluster_addons" {
  description = "Metadatos no secretos para ../addons, sin leer state."
  value = {
    account_id              = var.account_id
    aws_region              = var.aws_region
    aws_profile             = var.aws_profile
    deployment_role_arn     = var.deployment_role_arn
    cluster_name            = module.eks.cluster_name
    vpc_id                  = var.network_context.vpc_id
    alb_controller_role_arn = aws_iam_role.alb_controller.arn
  }
}

output "deployment_context" {
  description = "Solo metadatos para preparar los manifiestos reales; nunca DSN o PEM."
  value = {
    account_id   = var.account_id
    region       = var.aws_region
    cluster_name = module.eks.cluster_name
    api_endpoint = module.api_gateway.api_endpoint
    jwt_issuer   = module.api_gateway.issuer
    jwt_audience = module.api_gateway.jwt_audience
    ecr_repositories = {
      for service, repository in module.ecr : service => repository.repository_url
    }
    identidad_role_arn = module.identidad_irsa.role_arn
    target_group_arns = {
      cotizacion = module.api_gateway.target_group_arn
      identidad  = module.api_gateway.identidad_target_group_arn
      bff-web    = module.api_gateway.bff_web_target_group_arn
    }
  }
}

output "database_context" {
  description = "Metadatos privados del destino; no contiene credenciales master ni runtime."
  value = {
    host     = module.database.endpoint
    port     = 5432
    database = "solventa_prod"
  }
}

output "secret_arns" {
  description = "Ubicaciones, no valores; solo para operador autorizado por canal protegido."
  value = {
    database_master  = module.database.master_user_secret_arn
    database_domains = aws_secretsmanager_secret.database_domains.arn
    redis            = module.redis.secret_arn
    jwt              = module.jwt_key.secret_arn
  }
}

output "pipelines" {
  description = "Pipelines seleccionados con Deploy sujeto a aprobacion manual."
  value       = { for service, pipeline in module.cicd : service => pipeline.pipeline_name }
}

output "additional_node_groups" {
  description = "Grupos adicionales de la transicion; el grupo small original se conserva."
  value       = module.eks.additional_node_groups
}
