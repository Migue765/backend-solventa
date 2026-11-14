variable "aws_region" {
  description = "Region AWS."
  type        = string
  default     = "us-east-2"
  validation {
    condition     = var.aws_region == "us-east-2"
    error_message = "Esta reconstruccion esta autorizada solo en us-east-2."
  }
}

variable "aws_profile" {
  description = "Perfil AWS CLI de un rol asumido de despliegue, nunca root."
  type        = string
  validation {
    condition     = length(trimspace(var.aws_profile)) > 0
    error_message = "Indicar el perfil real del rol de despliegue."
  }
}

variable "cicd_automatic_triggers_enabled" {
  description = "Habilita los disparadores automaticos de todos los pipelines. Pausados temporalmente para limitar el consumo de CodePipeline y CodeBuild."
  type        = bool
  default     = false
}

variable "account_id" {
  description = "ID de la cuenta AWS permitida (evita aplicar en otra cuenta)."
  type        = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "account_id debe identificar explicitamente la cuenta AWS autorizada."
  }
}

variable "permissions_boundary_arns" {
  description = "Tres boundaries externas por familia: red (3 roles), CI (27) y aplicaciones (3). No se administran aqui."
  type = object({
    network     = string
    ci          = string
    application = string
  })
  nullable = false
  validation {
    condition = (
      length(toset(values(var.permissions_boundary_arns))) == 3 &&
      alltrue([
        for arn in values(var.permissions_boundary_arns) :
        can(regex("^arn:aws:iam::${var.account_id}:policy/[A-Za-z0-9+=,.@_/-]+$", arn))
      ])
    )
    error_message = "Indicar tres managed boundary ARN distintos de account_id, sin vacios/comodines/policies AWS. El ARN no acredita el aislamiento."
  }
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

variable "network_context" {
  description = "Output revisado del root network ya aplicado; IDs reales sin leer su state."
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
      cicd = object({
        cotizacion     = string
        bff-web        = string
        bff-movil      = string
        identidad      = string
        perfilamiento  = string
        polizas        = string
        siniestros     = string
        notificaciones = string
        asistencias    = string
      })
    })
  })

  validation {
    condition = (
      var.account_id == "396383602233" &&
      var.network_context.account_id == var.account_id &&
      var.network_context.region == var.aws_region &&
      var.network_context.name_prefix == var.name_prefix &&
      can(regex("^vpc-([0-9a-f]{8}|[0-9a-f]{17})$", var.network_context.vpc_id)) &&
      var.network_context.vpc_arn == "arn:aws:ec2:${var.aws_region}:${var.account_id}:vpc/${var.network_context.vpc_id}"
    )
    error_message = "network_context debe pertenecer a la cuenta/region/prefijo autorizados y contener ID/ARN VPC coherentes."
  }

  validation {
    condition = (
      length(var.network_context.public_subnet_ids) == 2 &&
      length(var.network_context.private_subnet_ids) == 2 &&
      length(toset(concat(var.network_context.public_subnet_ids, var.network_context.private_subnet_ids))) == 4 &&
      alltrue([
        for id in concat(var.network_context.public_subnet_ids, var.network_context.private_subnet_ids) :
        can(regex("^subnet-([0-9a-f]{8}|[0-9a-f]{17})$", id))
      ])
    )
    error_message = "Indicar dos subnets publicas y dos privadas, distintas y con IDs reales; las AZ y pertenencia se comprueban por metadatos."
  }

  validation {
    condition = (
      length(toset(concat([
        var.network_context.security_group_ids.db, var.network_context.security_group_ids.redis,
        var.network_context.security_group_ids.alb, var.network_context.security_group_ids.vpc_link
      ], values(var.network_context.security_group_ids.cicd)))) == 13 &&
      alltrue([
        for id in concat([
          var.network_context.security_group_ids.db, var.network_context.security_group_ids.redis,
          var.network_context.security_group_ids.alb, var.network_context.security_group_ids.vpc_link
        ], values(var.network_context.security_group_ids.cicd)) :
        can(regex("^sg-([0-9a-f]{8}|[0-9a-f]{17})$", id))
      ])
    )
    error_message = "Indicar los trece SG distintos de network; vacios, placeholders y duplicados no son validos."
  }
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
  description = "Roles IAM de esta cuenta con admin de cluster. Incluir el rol real del perfil de despliegue."
  type        = list(string)
  validation {
    condition = length(var.eks_admin_principal_arns) > 0 && alltrue([
      for arn in var.eks_admin_principal_arns : can(regex("^arn:aws:iam::${var.account_id}:role/.+$", arn))
    ])
    error_message = "Indicar roles IAM reales de account_id; no se admiten root, usuarios o ARNs de otra cuenta."
  }
}

variable "node_instance_types" {
  description = "Tipos de instancia de los nodos."
  type        = list(string)
  default     = ["t3.small"]
}

variable "node_min_size" {
  description = "Minimo de nodos."
  type        = number
  default     = 3
}

variable "node_desired_size" {
  description = "Nodos deseados."
  type        = number
  default     = 3
}

variable "node_max_size" {
  description = "Maximo de nodos del entorno academico sin trafico real."
  type        = number
  default     = 3
}

variable "db_name" {
  description = "Base de datos logica compartida por todos los servicios (letras, numeros y guion bajo)."
  type        = string
  default     = "solventa_prod"
}

variable "db_instance_class" {
  description = "Clase RDS PostgreSQL; comprobar costo y elegibilidad del plan de cuenta."
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
  default     = true
}

variable "db_skip_final_snapshot" {
  description = "Omitir snapshot final de la base al destruir (poner en false antes del lanzamiento)."
  type        = bool
  default     = false
}

variable "ecr_force_delete" {
  description = "Permite borrar el repo ECR con imagenes (poner en false antes del lanzamiento)."
  type        = bool
  default     = false
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
  default     = true
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
