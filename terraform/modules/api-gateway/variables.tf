variable "name" {
  description = "Prefijo de nombres (ej. solventa-prod)."
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

variable "bff_web_path_patterns" {
  description = "Patrones de path del ALB que se desvian a bff-web (max. 5 por regla). El resto va a cotizacion."
  type        = list(string)
  default = [
    "/v1/sesiones*",
    "/v1/usuarios*",
    "/v1/productos-seguro*",
    "/v1/solicitudes-cotizacion*",
    "/v1/clientes/*",
  ]

  validation {
    condition     = length(var.bff_web_path_patterns) >= 1 && length(var.bff_web_path_patterns) <= 5
    error_message = "Una regla de listener admite entre 1 y 5 patrones de path."
  }
}

variable "auth_throttling_rate_limit" {
  description = "Limite (req/s) de login, renovacion y registro, web y movil (proteccion contra fuerza bruta). Es global por ruta, no por cliente."
  type        = number
  default     = 5
}

variable "auth_throttling_burst_limit" {
  description = "Burst de login, renovacion y registro, web y movil."
  type        = number
  default     = 10
}

variable "bff_movil_path_patterns" {
  description = "Patrones de path del ALB que se desvian a bff-movil. Deben ser disjuntos de bff_web_path_patterns."
  type        = list(string)
  default     = ["/movil/*"]

  validation {
    condition     = length(var.bff_movil_path_patterns) >= 1 && length(var.bff_movil_path_patterns) <= 5
    error_message = "Una regla de listener admite entre 1 y 5 patrones de path."
  }
}

variable "identidad_path_patterns" {
  description = "Patrones de path del ALB que se desvian a identidad (solo discovery OIDC y JWKS)."
  type        = list(string)
  default     = ["/.well-known/*"]

  validation {
    condition     = length(var.identidad_path_patterns) >= 1 && length(var.identidad_path_patterns) <= 5
    error_message = "Una regla de listener admite entre 1 y 5 patrones de path."
  }
}

variable "jwt_audience" {
  description = "Audiencia (claim aud) que el authorizer JWT exige a los access tokens."
  type        = string
  default     = "solventa-api"
}

variable "cors_allowed_origins" {
  description = "Origenes de navegador autorizados por CORS (el gateway responde los preflight)."
  type        = list(string)
  default = [
    "https://jazmincorandes.github.io",
    "http://localhost:4200",
  ]
}
