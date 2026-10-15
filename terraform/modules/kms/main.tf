data "aws_caller_identity" "current" {}

# Una sola CMK para RDS, S3, Secrets Manager, SNS y SQS (1 llave = ~1 USD/mes).
data "aws_iam_policy_document" "key" {
  statement {
    sid       = "EnableIAMPermissions"
    effect    = "Allow"
    actions   = ["kms:*"]
    resources = ["*"]

    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"]
    }
  }

  dynamic "statement" {
    for_each = length(var.service_principals) > 0 ? [1] : []

    content {
      sid       = "AllowServicePrincipals"
      effect    = "Allow"
      actions   = ["kms:GenerateDataKey*", "kms:Decrypt"]
      resources = ["*"]

      principals {
        type        = "Service"
        identifiers = var.service_principals
      }

      condition {
        test     = "StringEquals"
        variable = "aws:SourceAccount"
        values   = [data.aws_caller_identity.current.account_id]
      }
    }
  }
}

resource "aws_kms_key" "this" {
  description             = "${var.name} CMK (RDS, S3, Secrets Manager, SNS, SQS)"
  enable_key_rotation     = true
  deletion_window_in_days = var.deletion_window_in_days
  policy                  = data.aws_iam_policy_document.key.json

  tags = merge(var.tags, { Name = "${var.name}-${var.alias_suffix}" })
}

resource "aws_kms_alias" "this" {
  name          = "alias/${var.name}-${var.alias_suffix}"
  target_key_id = aws_kms_key.this.key_id
}
