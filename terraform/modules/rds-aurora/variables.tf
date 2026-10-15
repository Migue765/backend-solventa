variable "name" {
  description = "Prefijo de nombres (ej. solventa-dev)."
  type        = string
}

variable "vpc_id" {
  description = "VPC donde se crea el cluster."
  type        = string
}

variable "subnet_ids" {
  description = "Subnets privadas para el subnet group (>= 2 AZ)."
  type        = list(string)
}

variable "allowed_security_group_ids" {
  description = "Security groups autorizados a conectar al 5432 (SG de los nodos EKS)."
  type        = list(string)
}

variable "kms_key_arn" {
  description = "ARN de la CMK para cifrar almacenamiento y el secreto master."
  type        = string
}

variable "engine_version" {
  description = "Version de Aurora PostgreSQL."
  type        = string
  default     = "17.9"
}

variable "database_name" {
  description = "Base de datos inicial (una por dominio)."
  type        = string
  default     = "cotizacion"
}

variable "master_username" {
  description = "Usuario master (la contrasena la gestiona Secrets Manager)."
  type        = string
  default     = "solventa_admin"
}

variable "min_capacity" {
  description = "ACU minimas de Serverless v2 (0.5 minimo; 0 habilita auto-pause si la version lo soporta)."
  type        = number
  default     = 0.5
}

variable "max_capacity" {
  description = "ACU maximas de Serverless v2."
  type        = number
  default     = 2
}

variable "backup_retention_period" {
  description = "Dias de retencion de backups."
  type        = number
  default     = 7
}

variable "deletion_protection" {
  description = "Proteccion contra borrado (false solo en dev)."
  type        = bool
  default     = true
}

variable "skip_final_snapshot" {
  description = "Omitir snapshot final al destruir (true solo en dev)."
  type        = bool
  default     = false
}

variable "tags" {
  description = "Tags comunes."
  type        = map(string)
  default     = {}
}
