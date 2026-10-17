output "api_endpoint" {
  description = "URL base del HTTP API."
  value       = aws_apigatewayv2_api.this.api_endpoint
}

output "api_id" {
  description = "ID del HTTP API."
  value       = aws_apigatewayv2_api.this.id
}

output "alb_arn" {
  description = "ARN del ALB interno."
  value       = aws_lb.internal.arn
}

output "alb_dns_name" {
  description = "DNS del ALB interno."
  value       = aws_lb.internal.dns_name
}

output "alb_security_group_id" {
  description = "SG del ALB interno."
  value       = aws_security_group.alb.id
}

output "target_group_arn" {
  description = "ARN del target group (usar en TargetGroupBinding.spec.targetGroupARN)."
  value       = aws_lb_target_group.service.arn
}

output "bff_web_target_group_arn" {
  description = "ARN del target group de bff-web (usar en su TargetGroupBinding)."
  value       = aws_lb_target_group.bff_web.arn
}

output "bff_movil_target_group_arn" {
  description = "ARN del target group de bff-movil (usar en su TargetGroupBinding)."
  value       = aws_lb_target_group.bff_movil.arn
}

output "identidad_target_group_arn" {
  description = "ARN del target group de identidad (usar en su TargetGroupBinding)."
  value       = aws_lb_target_group.identidad.arn
}

output "issuer" {
  description = "Emisor (claim iss) de los JWT: la URL base del API, sin barra final."
  value       = aws_apigatewayv2_api.this.api_endpoint
}

output "jwt_audience" {
  description = "Audiencia (claim aud) de los JWT."
  value       = var.jwt_audience
}
