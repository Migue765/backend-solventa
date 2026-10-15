locals {
  oidc_host = replace(var.oidc_issuer_url, "https://", "")
}

# ---------------------------------------------------------------------------
# Rol IRSA: AWS Load Balancer Controller
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "alb_controller_assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [var.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:sub"
      values   = ["system:serviceaccount:${var.alb_controller_namespace}:${var.alb_controller_service_account}"]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "alb_controller" {
  name               = "${var.name}-alb-controller"
  assume_role_policy = data.aws_iam_policy_document.alb_controller_assume.json

  tags = var.tags
}

resource "aws_iam_policy" "alb_controller" {
  name        = "${var.name}-alb-controller"
  description = "Politica oficial del AWS Load Balancer Controller"
  policy      = file("${path.module}/alb-controller-policy.json")

  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "alb_controller" {
  role       = aws_iam_role.alb_controller.name
  policy_arn = aws_iam_policy.alb_controller.arn
}

# ---------------------------------------------------------------------------
# Rol IRSA: servicio cotizacion
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "service_assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [var.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:sub"
      values   = ["system:serviceaccount:${var.service_namespace}:${var.service_account_name}"]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "service" {
  name               = "${var.name}-cotizacion"
  assume_role_policy = data.aws_iam_policy_document.service_assume.json

  tags = var.tags
}

data "aws_iam_policy_document" "service" {
  statement {
    sid       = "ReadDbSecret"
    actions   = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
    resources = [var.db_secret_arn]
  }

  statement {
    sid       = "WriteEvidence"
    actions   = ["s3:PutObject", "s3:GetObject", "s3:GetObjectVersion"]
    resources = ["${var.evidence_bucket_arn}/*"]
  }

  statement {
    sid       = "ListEvidenceBucket"
    actions   = ["s3:ListBucket"]
    resources = [var.evidence_bucket_arn]
  }

  statement {
    sid       = "PublishDecisionEvents"
    actions   = ["sns:Publish"]
    resources = [var.sns_topic_arn]
  }

  statement {
    sid       = "UseCmk"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
    resources = [var.kms_key_arn]
  }
}

resource "aws_iam_policy" "service" {
  name        = "${var.name}-cotizacion"
  description = "Permisos minimos del servicio cotizacion"
  policy      = data.aws_iam_policy_document.service.json

  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "service" {
  role       = aws_iam_role.service.name
  policy_arn = aws_iam_policy.service.arn
}
