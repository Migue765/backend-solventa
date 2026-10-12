locals {
  topic_name = "${var.name}-${var.topic_name}"
  queue_name = "${var.name}-${var.topic_name}"
}

resource "aws_sns_topic" "this" {
  name              = local.topic_name
  kms_master_key_id = var.kms_key_arn

  tags = var.tags
}

resource "aws_sqs_queue" "dlq" {
  name                      = "${local.queue_name}-dlq"
  kms_master_key_id         = var.kms_key_arn
  message_retention_seconds = var.dlq_message_retention_seconds

  tags = var.tags
}

resource "aws_sqs_queue" "this" {
  name                       = local.queue_name
  kms_master_key_id          = var.kms_key_arn
  visibility_timeout_seconds = var.visibility_timeout_seconds
  message_retention_seconds  = var.message_retention_seconds

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = var.max_receive_count
  })

  tags = var.tags
}

resource "aws_sqs_queue_redrive_allow_policy" "dlq" {
  queue_url = aws_sqs_queue.dlq.id

  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.this.arn]
  })
}

# Solo este topic puede enviar a la cola.
data "aws_iam_policy_document" "queue" {
  statement {
    sid       = "AllowSnsTopicSendMessage"
    effect    = "Allow"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.this.arn]

    principals {
      type        = "Service"
      identifiers = ["sns.amazonaws.com"]
    }

    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_sns_topic.this.arn]
    }
  }
}

resource "aws_sqs_queue_policy" "this" {
  queue_url = aws_sqs_queue.this.id
  policy    = data.aws_iam_policy_document.queue.json
}

resource "aws_sns_topic_subscription" "queue" {
  topic_arn            = aws_sns_topic.this.arn
  protocol             = "sqs"
  endpoint             = aws_sqs_queue.this.arn
  raw_message_delivery = true

  depends_on = [aws_sqs_queue_policy.this]
}
