output "state_bucket_name" {
  description = "Bucket de estado remoto (usar en el backend s3 de los ambientes)."
  value       = aws_s3_bucket.state.id
}

output "state_bucket_arn" {
  description = "ARN del bucket de estado."
  value       = aws_s3_bucket.state.arn
}
