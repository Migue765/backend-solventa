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
