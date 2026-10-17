output "role_arn" {
  description = "ARN del rol IRSA (anotacion eks.amazonaws.com/role-arn del ServiceAccount)."
  value       = aws_iam_role.this.arn
}
