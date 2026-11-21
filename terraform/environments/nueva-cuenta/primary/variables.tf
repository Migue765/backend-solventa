variable "account_id" {
  description = "Cuenta nueva autorizada, sin seleccion implicita."
  type        = string
  nullable    = false
  validation {
    condition     = var.account_id == "096571798698"
    error_message = "Este entorno solo corresponde a la cuenta nueva 096571798698."
  }
}

variable "aws_region" {
  description = "Region autorizada."
  type        = string
  default     = "us-east-2"
  nullable    = false
  validation {
    condition     = var.aws_region == "us-east-2"
    error_message = "Este entorno solo corresponde a us-east-2."
  }
}

variable "aws_profile" {
  description = "Perfil local del rol asumido; no el perfil directo solventa-admin."
  type        = string
  nullable    = false
  validation {
    condition     = length(trimspace(var.aws_profile)) > 0
    error_message = "Indicar el perfil real del rol de despliegue."
  }
}

variable "deployment_role_arn" {
  description = "ARN IAM estable del rol de despliegue aprobado, no ARN STS ni usuario."
  type        = string
  nullable    = false
  validation {
    condition     = can(regex("^arn:aws:iam::096571798698:role/([A-Za-z0-9+=,.@_-]+/)*[A-Za-z0-9+=,.@_-]+$", var.deployment_role_arn))
    error_message = "Indicar el ARN IAM del rol aprobado de 096571798698."
  }
}

variable "name_prefix" {
  type    = string
  default = "solventa-nueva"
}

variable "network_context" {
  description = "Output nombrado y revisado de ../network; nunca su state."
  nullable    = false
  type = object({
    account_id         = string
    region             = string
    name_prefix        = string
    vpc_id             = string
    vpc_arn            = string
    public_subnet_ids  = list(string)
    private_subnet_ids = list(string)
    security_group_ids = object({
      db       = string
      redis    = string
      alb      = string
      vpc_link = string
      cicd     = map(string)
    })
  })
  validation {
    condition = (
      var.network_context.account_id == "096571798698" &&
      var.network_context.region == "us-east-2" &&
      var.network_context.name_prefix == var.name_prefix &&
      can(regex("^vpc-([0-9a-f]{8}|[0-9a-f]{17})$", var.network_context.vpc_id)) &&
      var.network_context.vpc_arn == "arn:aws:ec2:us-east-2:096571798698:vpc/${var.network_context.vpc_id}" &&
      length(var.network_context.public_subnet_ids) == 2 &&
      length(var.network_context.private_subnet_ids) == 2 &&
      length(toset(concat(var.network_context.public_subnet_ids, var.network_context.private_subnet_ids))) == 4 &&
      toset(keys(var.network_context.security_group_ids.cicd)) == toset([
        "asistencias", "bff-movil", "bff-web", "cotizacion", "identidad",
        "notificaciones", "perfilamiento", "polizas", "siniestros",
      ]) &&
      length(toset(concat([
        var.network_context.security_group_ids.db, var.network_context.security_group_ids.redis,
        var.network_context.security_group_ids.alb, var.network_context.security_group_ids.vpc_link,
      ], values(var.network_context.security_group_ids.cicd)))) == 13
    )
    error_message = "network_context debe contener la red nueva de esta cuenta, dos pares de subnets y trece SG distintos."
  }
}

variable "permissions_boundary_arns" {
  description = "Boundaries externas revisadas; indicar ARN no acredita su contenido ni aislamiento."
  nullable    = false
  type = object({
    network     = string
    ci          = string
    application = string
  })
  validation {
    condition = (
      length(toset(values(var.permissions_boundary_arns))) == 3 &&
      alltrue([
        for arn in values(var.permissions_boundary_arns) :
        can(regex("^arn:aws:iam::096571798698:policy/[A-Za-z0-9+=,.@_/-]+$", arn))
      ])
    )
    error_message = "Se requieren tres boundary ARN distintos de la cuenta nueva, sin vacios ni comodines."
  }
}

variable "public_access_cidrs" {
  description = "IPv4 /32 aprobadas para administrar EKS; no ingreso funcional de la aplicacion."
  type        = list(string)
  validation {
    condition = length(var.public_access_cidrs) > 0 && alltrue([
      for cidr in var.public_access_cidrs : can(cidrnetmask(cidr)) && endswith(cidr, "/32")
    ])
    error_message = "Indicar al menos una IPv4 /32 autorizada para gestion."
  }
}

variable "kubernetes_version" {
  description = "Confirmar soporte estandar y compatibilidad de addons en Ohio antes de plan real."
  type        = string
  default     = "1.35"
}

variable "node_count" {
  description = "Dos nodos compartidos para el primer recorrido; revisar capacidad antes de ampliar a nueve APIs."
  type        = number
  default     = 2
  validation {
    condition     = var.node_count >= 2 && var.node_count == floor(var.node_count)
    error_message = "Se requieren al menos dos nodos compartidos, no uno por servicio."
  }
}

variable "enable_small_node_group" {
  description = "Agrega dos small al grupo original de dos, para un total de cuatro nodos."
  type        = bool
  default     = false
  nullable    = false
  validation {
    condition     = !var.enable_small_node_group || (var.node_count == 2 && !var.enable_medium_node_group)
    error_message = "La ampliacion revisada conserva dos small y agrega dos small, sin medium."
  }
}

variable "small_node_release_version" {
  description = "Release AMI observada en los small actuales; fijarla antes de ampliar."
  type        = string
  default     = null
  validation {
    condition = !var.enable_small_node_group || (
      can(regex("^[0-9]+\\.[0-9]+\\.[0-9]+-[0-9]{8}$", var.small_node_release_version)) &&
      try(startswith(var.small_node_release_version, "${var.kubernetes_version}."), false)
    )
    error_message = "La ampliacion small requiere una release AMI explicita compatible con kubernetes_version."
  }
}

variable "enable_medium_node_group" {
  description = "Fase A aprobada: agrega dos medium conservando los dos small. No habilita su retirada."
  type        = bool
  default     = false
  nullable    = false
  validation {
    condition     = !var.enable_medium_node_group || var.node_count == 2
    error_message = "La transicion revisada mantiene dos small y agrega exactamente dos medium."
  }
}

variable "medium_node_release_version" {
  description = "Release AMI observada en el grupo actual; fijarla antes de habilitar medium."
  type        = string
  default     = null
  validation {
    condition = !var.enable_medium_node_group || (
      can(regex("^[0-9]+\\.[0-9]+\\.[0-9]+-[0-9]{8}$", var.medium_node_release_version)) &&
      try(startswith(var.medium_node_release_version, "${var.kubernetes_version}."), false)
    )
    error_message = "La fase A requiere una release AMI explicita compatible con kubernetes_version."
  }
}

variable "api_listener_protocol" {
  description = "Decision explicita para VPC Link -> ALB: HTTP privado o HTTPS con certificado."
  type        = string
  nullable    = false
  validation {
    condition     = contains(["HTTP", "HTTPS"], var.api_listener_protocol)
    error_message = "Elegir HTTP o HTTPS para el tramo interno."
  }
}

variable "api_certificate_arn" {
  type    = string
  default = null
  validation {
    condition = var.api_certificate_arn == null || can(regex(
      "^arn:aws:acm:us-east-2:096571798698:certificate/[0-9a-f-]+$", var.api_certificate_arn
    ))
    error_message = "El certificado debe pertenecer a la cuenta/region nuevas."
  }
}

variable "api_tls_server_name" {
  type    = string
  default = null
  validation {
    condition = var.api_tls_server_name == null || can(regex(
      "^[A-Za-z0-9-]+(\\.[A-Za-z0-9-]+)+$", var.api_tls_server_name
    ))
    error_message = "Indicar un nombre DNS verificable, no vacio ni URL."
  }
}

variable "release_branch" {
  description = "Rama aprobada de los SHA que construiran los pipelines."
  type        = string
  validation {
    condition     = length(trimspace(var.release_branch)) > 0
    error_message = "Indicar la rama de release aprobada; no se presupone main."
  }
}

variable "pipeline_services" {
  description = "Pipelines del primer recorrido; [] permite diferir su creacion y posibles builds iniciales."
  type        = set(string)
  default     = ["identidad", "bff-web", "perfilamiento"]
  validation {
    condition = length(setsubtract(var.pipeline_services, toset([
      "identidad", "bff-web", "perfilamiento", "cotizacion",
    ]))) == 0
    error_message = "Preparar dependencias de dominio antes de incorporar pipelines de otras APIs."
  }
}

variable "adopt_created_ecr" {
  description = "Adoptar solo los nueve ECR y lifecycle creados por peticion expresa en esta cuenta nueva."
  type        = bool
  default     = false
  nullable    = false
}

variable "enable_jwt_routes" {
  description = "Activar tras publicar discovery/JWKS reales. False deja ausentes las rutas protegidas."
  type        = bool
  default     = true
  nullable    = false
}
