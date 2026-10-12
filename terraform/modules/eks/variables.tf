variable "cluster_name" {
  description = "Nombre del cluster EKS."
  type        = string
}

variable "kubernetes_version" {
  description = "Version de Kubernetes."
  type        = string
  default     = "1.33"
}

variable "vpc_id" {
  description = "VPC del cluster."
  type        = string
}

variable "private_subnet_ids" {
  description = "Subnets privadas (>= 2 AZ) para el control plane y los nodos."
  type        = list(string)
}

variable "public_access_cidrs" {
  description = "CIDRs autorizados al endpoint publico del API server (ej. [\"203.0.113.10/32\"])."
  type        = list(string)

  validation {
    condition     = length(var.public_access_cidrs) > 0 && !contains(var.public_access_cidrs, "0.0.0.0/0")
    error_message = "public_access_cidrs no puede estar vacio ni contener 0.0.0.0/0."
  }
}

variable "admin_principal_arns" {
  description = "ARNs IAM (usuarios/roles) con rol de admin de cluster via access entries."
  type        = list(string)
}

variable "node_instance_types" {
  description = "Tipos de instancia del managed node group."
  type        = list(string)
  default     = ["t3.small"]
}

variable "node_min_size" {
  description = "Minimo de nodos."
  type        = number
  default     = 2
}

variable "node_desired_size" {
  description = "Nodos deseados (inicial; luego lo gestiona el autoscaling)."
  type        = number
  default     = 2
}

variable "node_max_size" {
  description = "Maximo de nodos."
  type        = number
  default     = 4
}

variable "node_disk_size" {
  description = "Disco de cada nodo (GiB)."
  type        = number
  default     = 30
}

variable "addons" {
  description = "Add-ons gestionados de EKS (version null = la default del cluster)."
  type        = map(string)
  default = {
    "vpc-cni"    = null
    "coredns"    = null
    "kube-proxy" = null
  }
}

variable "tags" {
  description = "Tags comunes."
  type        = map(string)
  default     = {}
}
