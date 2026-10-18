variable "aws_region" {
  description = "Region AWS."
  type        = string
  default     = "us-east-2"
}

variable "aws_profile" {
  description = "Perfil de AWS CLI."
  type        = string
  default     = "tesis"
}

variable "account_id" {
  description = "ID de la cuenta AWS permitida (evita aplicar en otra cuenta)."
  type        = string
  default     = "976083331509"
}

variable "environment" {
  description = "Nombre del ambiente."
  type        = string
  default     = "prod"
}

variable "name_prefix" {
  description = "Prefijo de nombres de recursos."
  type        = string
  default     = "solventa-prod"
}

variable "vpc_cidr" {
  description = "CIDR de la VPC."
  type        = string
  default     = "10.20.0.0/16"
}

variable "kubernetes_version" {
  description = "Version de Kubernetes del cluster EKS."
  type        = string
  default     = "1.35"
}

variable "public_access_cidrs" {
  description = "CIDRs autorizados al endpoint publico del API server de EKS (tu IP /32)."
  type        = list(string)
}

variable "eks_admin_principal_arns" {
  description = "Principals IAM con admin de cluster (access entries). Debe incluir a quien ejecuta terraform/helm."
  type        = list(string)
  default     = ["arn:aws:iam::976083331509:user/Migue", "arn:aws:iam::976083331509:root"]
}

variable "node_instance_types" {
  description = "Tipos de instancia de los nodos."
  type        = list(string)
  default     = ["t3.small"]
}

variable "node_min_size" {
  description = "Minimo de nodos."
  type        = number
  default     = 2
}

variable "node_desired_size" {
  description = "Nodos deseados."
  type        = number
  default     = 2
}

variable "node_max_size" {
  description = "Maximo de nodos."
  type        = number
  default     = 3
}

variable "db_name" {
  description = "Base de datos logica compartida por todos los servicios (letras, numeros y guion bajo)."
  type        = string
  default     = "solventa_prod"
}

variable "db_instance_class" {
  description = "Clase de instancia de RDS PostgreSQL (db.t4g.micro entra en la capa gratuita)."
  type        = string
  default     = "db.t4g.micro"
}

variable "db_allocated_storage" {
  description = "Almacenamiento de la base en GB."
  type        = number
  default     = 20
}

variable "db_deletion_protection" {
  description = "Proteccion contra borrado de la base (activar antes del lanzamiento)."
  type        = bool
  default     = false
}

variable "db_skip_final_snapshot" {
  description = "Omitir snapshot final de la base al destruir (poner en false antes del lanzamiento)."
  type        = bool
  default     = true
}

variable "ecr_force_delete" {
  description = "Permite borrar el repo ECR con imagenes (poner en false antes del lanzamiento)."
  type        = bool
  default     = true
}

variable "object_lock_mode" {
  description = "Modo Object Lock del bucket de evidencias (GOVERNANCE o COMPLIANCE)."
  type        = string
  default     = "GOVERNANCE"
}

variable "object_lock_retention_days" {
  description = "Dias de retencion Object Lock."
  type        = number
  default     = 1
}

variable "api_listener_protocol" {
  description = "Protocolo del listener del ALB interno (HTTP o HTTPS)."
  type        = string
  default     = "HTTP"
}

variable "api_certificate_arn" {
  description = "Certificado ACM para el listener HTTPS (solo si api_listener_protocol = HTTPS)."
  type        = string
  default     = null
}

variable "api_tls_server_name" {
  description = "Nombre a verificar en el certificado del ALB (HTTPS)."
  type        = string
  default     = null
}

variable "api_throttling_rate_limit" {
  description = "Throttling por defecto del API (req/s)."
  type        = number
  default     = 50
}

variable "api_throttling_burst_limit" {
  description = "Burst por defecto del API."
  type        = number
  default     = 100
}

variable "service_namespace" {
  description = "Namespace Kubernetes del servicio cotizacion."
  type        = string
  default     = "cotizacion"
}

variable "service_account_name" {
  description = "ServiceAccount Kubernetes del servicio cotizacion."
  type        = string
  default     = "cotizacion"
}

variable "bff_web_namespace" {
  description = "Namespace Kubernetes del servicio bff-web."
  type        = string
  default     = "bff-web"
}

variable "bff_web_service_account_name" {
  description = "ServiceAccount Kubernetes del servicio bff-web."
  type        = string
  default     = "bff-web"
}

variable "redis_node_type" {
  description = "Tipo de nodo de ElastiCache Redis (limitador de intentos de identidad)."
  type        = string
  default     = "cache.t4g.micro"
}

variable "alb_controller_chart_version" {
  description = "Version del chart aws-load-balancer-controller."
  type        = string
  default     = "3.6.0"
}

variable "metrics_server_chart_version" {
  description = "Version del chart metrics-server."
  type        = string
  default     = "3.14.0"
}

variable "github_repository" {
  description = "Repositorio de GitHub (owner/repo) que dispara el pipeline de despliegue."
  type        = string
  default     = "Migue765/backend-solventa"
}

variable "release_branch" {
  description = "Rama de la que se toma el codigo de cada version."
  type        = string
  default     = "main"
}

variable "require_manual_approval" {
  description = "Exige aprobacion manual entre el build y el despliegue."
  type        = bool
  default     = false
}

variable "bff_web_release_tag_patterns" {
  description = "Tags de GitHub que disparan el pipeline de bff-web. Deben ser disjuntos de los de cotizacion (v*)."
  type        = list(string)
  default     = ["bff-web-v*"]
}

variable "bff_movil_namespace" {
  description = "Namespace Kubernetes del servicio bff-movil."
  type        = string
  default     = "bff-movil"
}

variable "bff_movil_service_account_name" {
  description = "ServiceAccount Kubernetes del servicio bff-movil."
  type        = string
  default     = "bff-movil"
}

variable "bff_movil_release_tag_patterns" {
  description = "Tags de GitHub que disparan el pipeline de bff-movil. Disjuntos de v* (cotizacion) y bff-web-v*."
  type        = list(string)
  default     = ["bff-movil-v*"]
}

variable "identidad_namespace" {
  description = "Namespace Kubernetes del servicio identidad."
  type        = string
  default     = "identidad"
}

variable "identidad_service_account_name" {
  description = "ServiceAccount Kubernetes del servicio identidad."
  type        = string
  default     = "identidad"
}

variable "identidad_release_tag_patterns" {
  description = "Tags de GitHub que disparan el pipeline de identidad. Disjuntos de v*, bff-web-v* y bff-movil-v*."
  type        = list(string)
  default     = ["identidad-v*"]
}

variable "jwt_audience" {
  description = "Audiencia (claim aud) de los access tokens; el authorizer del API Gateway la exige."
  type        = string
  default     = "solventa-api"
}

variable "cors_allowed_origins" {
  description = "Origenes de navegador autorizados por CORS en el API Gateway (frontend web)."
  type        = list(string)
  default = [
    "https://jazmincorandes.github.io",
    "http://localhost:4200",
  ]
}
