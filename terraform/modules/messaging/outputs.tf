output "topic_arn" {
  description = "ARN del topic SNS."
  value       = aws_sns_topic.this.arn
}

output "queue_arn" {
  description = "ARN de la cola SQS."
  value       = aws_sqs_queue.this.arn
}

output "queue_url" {
  description = "URL de la cola SQS."
  value       = aws_sqs_queue.this.id
}

output "dlq_arn" {
  description = "ARN de la DLQ."
  value       = aws_sqs_queue.dlq.arn
}
