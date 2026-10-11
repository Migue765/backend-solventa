variable "name" {
  description = "Prefijo de nombres."
  type        = string
}

variable "vpc_id" {
  description = "VPC donde se crea la base."
  type        = string
}

variable "subnet_ids" {
  description = "Subnets privadas del subnet group."
  type        = list(string)
}

variable "allowed_security_group_ids" {
  description = "SG autorizados a conectarse al puerto 5432 (nodos EKS)."
  type        = list(string)
}

variable "kms_key_arn" {
  description = "Llave KMS para cifrar almacenamiento y secreto."
  type        = string
}

variable "engine_version" {
  description = "Version mayor o completa de PostgreSQL."
  type        = string
  default     = "17"
}

variable "instance_class" {
  description = "Clase de instancia (db.t4g.micro es la minima y entra en la capa gratuita)."
  type        = string
  default     = "db.t4g.micro"
}

variable "allocated_storage" {
  description = "Almacenamiento en GB."
  type        = number
  default     = 20
}

variable "database_name" {
  description = "Nombre de la base de datos."
  type        = string
}

variable "master_username" {
  description = "Usuario master."
  type        = string
  default     = "cotizacion_admin"
}

variable "multi_az" {
  description = "Replica sincrona en otra zona (duplica el costo)."
  type        = bool
  default     = false
}

variable "backup_retention_period" {
  description = "Dias de retencion de backups automaticos."
  type        = number
  default     = 1
}

variable "deletion_protection" {
  description = "Proteccion contra borrado."
  type        = bool
  default     = false
}

variable "skip_final_snapshot" {
  description = "Omitir snapshot final al destruir."
  type        = bool
  default     = true
}

variable "tags" {
  description = "Tags comunes."
  type        = map(string)
  default     = {}
}
