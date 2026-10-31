variable "name" {
  description = "Prefijo de nombres (ej. solventa-prod)."
  type        = string
}

variable "service_name" {
  description = "Servicio que despliega este pipeline (nombre del Deployment, del pipeline y de los manifiestos)."
  type        = string
  default     = "cotizacion"
}

variable "service_dir" {
  description = "Directorio del servicio en el repo (default: services/<service_name>)."
  type        = string
  default     = null
}

variable "run_migrations" {
  description = "Ejecuta el Job de migraciones antes del rollout. false para servicios sin base de datos propia (ej. los BFF)."
  type        = bool
  default     = true
}

variable "resource_prefix" {
  description = "Prefijo de bucket, conexion, roles y SG (default: name). Usar uno distinto en la segunda instancia del modulo para evitar colisiones."
  type        = string
  default     = null
}

variable "github_connection_arn" {
  description = "Conexion con GitHub ya autorizada para reutilizar; si es null se crea una nueva."
  type        = string
  default     = null
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
  description = "Patrones de tag de GitHub que disparan el pipeline. Deben ser disjuntos entre servicios (ej. v* y bff-web-v*)."
  type        = list(string)
  default     = ["v*"]
}

variable "automatic_triggers_enabled" {
  description = "Habilita ejecuciones automaticas por tags. false conserva el pipeline solo para ejecuciones manuales."
  type        = bool
  default     = true
}

variable "require_manual_approval" {
  description = "Agrega una aprobacion manual entre el build y el despliegue."
  type        = bool
  default     = false
}

variable "restrict_service_access" {
  description = "Acota trust CodeBuild, conexion GitHub, ENI y salida TCP 443/80. false conserva los permisos y la red de las instancias existentes."
  type        = bool
  default     = false
}

variable "artifact_force_destroy" {
  description = "Permite eliminar el bucket con artefactos. false protege el bucket de la nueva instancia."
  type        = bool
  default     = true
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
