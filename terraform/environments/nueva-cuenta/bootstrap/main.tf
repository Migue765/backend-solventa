terraform {
  required_version = ">= 1.11"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region              = "us-east-2"
  profile             = var.aws_profile
  allowed_account_ids = ["096571798698"]
}

variable "aws_profile" {
  type = string
  validation {
    condition     = length(trimspace(var.aws_profile)) > 0
    error_message = "Indicar el perfil real del rol de despliegue."
  }
}

variable "deployment_role_arn" {
  type = string
  validation {
    condition     = can(regex("^arn:aws:iam::096571798698:role/([A-Za-z0-9+=,.@_-]+/)*[A-Za-z0-9+=,.@_-]+$", var.deployment_role_arn))
    error_message = "Indicar el ARN IAM estable del rol aprobado de la cuenta nueva."
  }
}

data "aws_caller_identity" "deployment" {
  lifecycle {
    postcondition {
      condition = self.account_id == "096571798698" && startswith(
        self.arn, "arn:aws:sts::096571798698:assumed-role/${element(reverse(split("/", var.deployment_role_arn)), 0)}/"
      )
      error_message = "Bootstrap requiere el rol asumido aprobado de la cuenta nueva."
    }
  }
}

resource "aws_s3_bucket" "state" {
  bucket = "solventa-tfstate-096571798698"
  lifecycle {
    prevent_destroy = true
  }
  depends_on = [data.aws_caller_identity.deployment]
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    id     = "expire-noncurrent-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = 90
    }
  }
}

data "aws_iam_policy_document" "tls_only" {
  statement {
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.state.arn, "${aws_s3_bucket.state.arn}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "state" {
  bucket     = aws_s3_bucket.state.id
  policy     = data.aws_iam_policy_document.tls_only.json
  depends_on = [aws_s3_bucket_public_access_block.state]
}

output "state_bucket" {
  value = aws_s3_bucket.state.bucket
}
