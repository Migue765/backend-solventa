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
  default     = "dev"
}

variable "name_prefix" {
  description = "Prefijo de nombres de recursos."
  type        = string
  default     = "solventa-dev"
}

variable "vpc_cidr" {
  description = "CIDR de la VPC."
  type        = string
  default     = "10.20.0.0/16"
}

variable "kubernetes_version" {
  description = "Version de Kubernetes del cluster EKS."
  type        = string
  default     = "1.33"
}

variable "public_access_cidrs" {
  description = "CIDRs autorizados al endpoint publico del API server de EKS (tu IP /32)."
  type        = list(string)
}

variable "eks_admin_principal_arns" {
  description = "Principals IAM con admin de cluster (access entries). Debe incluir a quien ejecuta terraform/helm."
  type        = list(string)
  default     = ["arn:aws:iam::976083331509:user/Migue"]
}

variable "node_instance_types" {
  description = "Tipos de instancia de los nodos."
  type        = list(string)
  default     = ["t3.medium"]
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
  default     = 4
}

variable "db_max_capacity" {
  description = "ACU maximas de Aurora Serverless v2."
  type        = number
  default     = 2
}

variable "db_min_capacity" {
  description = "ACU minimas de Aurora Serverless v2."
  type        = number
  default     = 0.5
}

variable "db_deletion_protection" {
  description = "Proteccion contra borrado de Aurora (false solo en dev)."
  type        = bool
  default     = false
}

variable "db_skip_final_snapshot" {
  description = "Omitir snapshot final de Aurora al destruir (true solo en dev)."
  type        = bool
  default     = true
}

variable "ecr_force_delete" {
  description = "Permite borrar el repo ECR con imagenes (solo dev)."
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

variable "alb_controller_chart_version" {
  description = "Version del chart aws-load-balancer-controller (null = la ultima)."
  type        = string
  default     = null
}

variable "metrics_server_chart_version" {
  description = "Version del chart metrics-server (null = la ultima)."
  type        = string
  default     = null
}
