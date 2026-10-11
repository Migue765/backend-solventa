variable "name" {
  description = "Prefijo de nombres (ej. solventa-dev)."
  type        = string
}

variable "topic_name" {
  description = "Nombre base del topic SNS."
  type        = string
  default     = "instantanea-decision-registrada"
}

variable "kms_key_arn" {
  description = "ARN de la CMK para cifrar SNS y SQS."
  type        = string
}

variable "max_receive_count" {
  description = "Intentos de recepcion antes de enviar a la DLQ."
  type        = number
  default     = 5
}

variable "visibility_timeout_seconds" {
  description = "Visibility timeout de la cola principal."
  type        = number
  default     = 60
}

variable "message_retention_seconds" {
  description = "Retencion de mensajes en la cola principal (default 4 dias)."
  type        = number
  default     = 345600
}

variable "dlq_message_retention_seconds" {
  description = "Retencion de mensajes en la DLQ (default 14 dias)."
  type        = number
  default     = 1209600
}

variable "tags" {
  description = "Tags comunes."
  type        = map(string)
  default     = {}
}
