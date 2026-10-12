variable "name" {
  description = "Prefijo de nombres (ej. solventa-prod)."
  type        = string
}

variable "github_repository" {
  description = "Repositorio de GitHub en formato owner/repo."
  type        = string
}

variable "branch_name" {
  description = "Rama de la que se toma el codigo (el tag debe apuntar a un commit de esta rama)."
  type        = string
  default     = "main"
}

variable "release_tag_patterns" {
  description = "Patrones de tag de GitHub que disparan el pipeline."
  type        = list(string)
  default     = ["v*"]
}

variable "require_manual_approval" {
  description = "Agrega una aprobacion manual entre el build y el despliegue."
  type        = bool
  default     = false
}

variable "vpc_id" {
  description = "VPC del cluster (CodeBuild de despliegue corre dentro para alcanzar el API server privado)."
  type        = string
}

variable "private_subnet_ids" {
  description = "Subnets privadas con salida a internet (NAT)."
  type        = list(string)
}

variable "cluster_name" {
  description = "Nombre del cluster EKS."
  type        = string
}

variable "cluster_security_group_id" {
  description = "SG del cluster EKS: recibe 443 desde CodeBuild."
  type        = string
}

variable "service_namespace" {
  description = "Namespace de Kubernetes donde CodeBuild puede desplegar."
  type        = string
  default     = "cotizacion"
}

variable "ecr_repository_arn" {
  description = "ARN del repositorio ECR."
  type        = string
}

variable "ecr_repository_url" {
  description = "URL del repositorio ECR."
  type        = string
}

variable "compute_type" {
  description = "Tamano de CodeBuild (el minimo entra en la capa gratuita)."
  type        = string
  default     = "BUILD_GENERAL1_SMALL"
}

variable "artifact_retention_days" {
  description = "Dias que se conservan artefactos y logs en S3."
  type        = number
  default     = 30
}

variable "tags" {
  description = "Tags comunes."
  type        = map(string)
  default     = {}
}
