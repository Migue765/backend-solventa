output "alb_controller_role_arn" {
  description = "ARN del rol IRSA del AWS Load Balancer Controller."
  value       = aws_iam_role.alb_controller.arn
}

output "service_role_arn" {
  description = "ARN del rol IRSA del servicio cotizacion (anotacion eks.amazonaws.com/role-arn)."
  value       = aws_iam_role.service.arn
}
