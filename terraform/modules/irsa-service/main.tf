data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  oidc_host = replace(var.oidc_issuer_url, "https://", "")
  accede_s3 = length(var.s3_object_arns) > 0
}

data "aws_iam_policy_document" "assume" {
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

resource "aws_iam_role" "this" {
  name               = var.name
  assume_role_policy = data.aws_iam_policy_document.assume.json

  tags = var.tags
}

data "aws_iam_policy_document" "this" {
  dynamic "statement" {
    for_each = length(var.secret_arns) > 0 ? [1] : []

    content {
      sid       = "LeerSecretos"
      actions   = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
      resources = var.secret_arns
    }
  }

  statement {
    sid       = "UsarCmk"
    actions   = ["kms:Decrypt", "kms:DescribeKey"]
    resources = [var.kms_key_arn]
  }

  # Sin s3:DeleteObjectVersion: un borrado deja un marcador y la version previa expira
  # por el ciclo de vida del bucket. s3:ResourceAccount evita escribir en buckets ajenos
  # con el mismo nombre.
  dynamic "statement" {
    for_each = local.accede_s3 ? [1] : []

    content {
      sid       = "ObjetosS3"
      actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
      resources = var.s3_object_arns

      condition {
        test     = "StringEquals"
        variable = "s3:ResourceAccount"
        values   = [data.aws_caller_identity.current.account_id]
      }
    }
  }

  dynamic "statement" {
    for_each = local.accede_s3 ? [1] : []

    content {
      sid       = "CifrarObjetosS3"
      actions   = ["kms:GenerateDataKey"]
      resources = [var.kms_key_arn]

      condition {
        test     = "StringEquals"
        variable = "kms:ViaService"
        values   = ["s3.${data.aws_region.current.name}.amazonaws.com"]
      }
    }
  }
}

resource "aws_iam_policy" "this" {
  name        = var.name
  description = "Permisos minimos del servicio ${var.service_namespace}/${var.service_account_name}"
  policy      = data.aws_iam_policy_document.this.json

  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "this" {
  role       = aws_iam_role.this.name
  policy_arn = aws_iam_policy.this.arn
}
