variable "name" {
  description = "Prefijo de nombres (ej. solventa-dev)."
  type        = string
}

variable "vpc_id" {
  description = "VPC del ALB interno y el VPC Link."
  type        = string
}

variable "private_subnet_ids" {
  description = "Subnets privadas (>= 2 AZ) para el ALB interno y el VPC Link."
  type        = list(string)
}

variable "target_security_group_id" {
  description = "SG de los pods/nodos EKS: se le permite ingreso desde el ALB al puerto del servicio."
  type        = string
}

variable "listener_protocol" {
  description = "Protocolo del listener del ALB (HTTP o HTTPS)."
  type        = string
  default     = "HTTP"

  validation {
    condition     = contains(["HTTP", "HTTPS"], var.listener_protocol)
    error_message = "listener_protocol debe ser HTTP o HTTPS."
  }
}

variable "certificate_arn" {
  description = "ARN del certificado ACM (obligatorio si listener_protocol = HTTPS)."
  type        = string
  default     = null
}

variable "tls_server_name" {
  description = "Nombre a verificar en el certificado del ALB cuando se usa HTTPS."
  type        = string
  default     = null
}

variable "target_port" {
  description = "Puerto de los pods."
  type        = number
  default     = 8080
}

variable "health_check_path" {
  description = "Ruta de health check del target group."
  type        = string
  default     = "/ping"
}

variable "throttling_rate_limit" {
  description = "Limite de solicitudes por segundo (default del stage)."
  type        = number
  default     = 50
}

variable "throttling_burst_limit" {
  description = "Burst de solicitudes (default del stage)."
  type        = number
  default     = 100
}

variable "tags" {
  description = "Tags comunes."
  type        = map(string)
  default     = {}
}
