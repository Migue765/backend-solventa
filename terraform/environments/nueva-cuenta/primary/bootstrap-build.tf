variable "bootstrap_build_sources" {
  description = "Build aislado sin pipeline; servicio => SHA256 de fuente inmutable en el bucket CI BFF existente."
  type        = map(string)
  default     = {}

  validation {
    condition = alltrue([
      for service, digest in var.bootstrap_build_sources :
      contains(local.api_services, service) && can(regex("^[0-9a-f]{64}$", digest))
    ])
    error_message = "Solo APIs de este entorno y una fuente SHA256 verificable."
  }

  validation {
    condition = length(var.bootstrap_build_sources) == 0 || (
      contains(var.pipeline_services, "bff-web") &&
      length(setintersection(toset(keys(var.bootstrap_build_sources)), var.pipeline_services)) == 0
    )
    error_message = "Requiere bucket BFF existente y no duplicar proyectos de pipelines completos."
  }
}

resource "aws_iam_role" "bootstrap_build" {
  for_each             = var.bootstrap_build_sources
  name                 = "${var.name_prefix}-${each.key}-codebuild-build"
  permissions_boundary = var.permissions_boundary_arns.ci
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "codebuild.amazonaws.com" }
      Condition = {
        StringEquals = { "aws:SourceAccount" = var.account_id }
        ArnEquals = {
          "aws:SourceArn" = "arn:aws:codebuild:${var.aws_region}:${var.account_id}:project/${var.name_prefix}-${each.key}-build"
        }
      }
    }]
  })
  tags = merge(local.tags, { Service = each.key })
}

resource "aws_iam_role_policy" "bootstrap_build" {
  for_each = var.bootstrap_build_sources
  name     = "build"
  role     = aws_iam_role.bootstrap_build[each.key].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:GetBucketLocation", "s3:GetBucketVersioning"]
        Resource = "arn:aws:s3:::${module.cicd["bff-web"].artifacts_bucket}"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:GetObjectVersion"]
        Resource = "arn:aws:s3:::${module.cicd["bff-web"].artifacts_bucket}/bootstrap-source/${each.value}.zip"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = "arn:aws:s3:::${module.cicd["bff-web"].artifacts_bucket}/logs/bootstrap/${each.key}/*"
      },
      {
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability", "ecr:BatchGetImage",
          "ecr:CompleteLayerUpload", "ecr:DescribeImages", "ecr:GetDownloadUrlForLayer",
          "ecr:InitiateLayerUpload", "ecr:PutImage", "ecr:UploadLayerPart",
        ]
        Resource = module.ecr[each.key].repository_arn
      },
    ]
  })
}

resource "aws_codebuild_project" "bootstrap_build" {
  for_each     = var.bootstrap_build_sources
  name         = "${var.name_prefix}-${each.key}-build"
  description  = "Build inicial aislado de ${each.key}; fuente S3 por SHA, sin pipeline GitHub."
  service_role = aws_iam_role.bootstrap_build[each.key].arn

  artifacts {
    type = "NO_ARTIFACTS"
  }
  source {
    type      = "S3"
    location  = "${module.cicd["bff-web"].artifacts_bucket}/bootstrap-source/${each.value}.zip"
    buildspec = "deploy/codebuild/buildspec-build.yml"
  }
  environment {
    compute_type    = "BUILD_GENERAL1_SMALL"
    image           = "aws/codebuild/standard:7.0"
    type            = "LINUX_CONTAINER"
    privileged_mode = true
    environment_variable {
      name  = "SERVICE_NAME"
      value = each.key
    }
    environment_variable {
      name  = "SERVICE_DIR"
      value = "services/${each.key}"
    }
    environment_variable {
      name  = "ECR_REPOSITORY_URL"
      value = module.ecr[each.key].repository_url
    }
    environment_variable {
      name  = "COMMIT_ID"
      value = each.value
    }
  }
  logs_config {
    cloudwatch_logs {
      status = "DISABLED"
    }
    s3_logs {
      status              = "ENABLED"
      encryption_disabled = false
      location            = "${module.cicd["bff-web"].artifacts_bucket}/logs/bootstrap/${each.key}"
    }
  }
  tags       = merge(local.tags, { Service = each.key })
  depends_on = [aws_iam_role_policy.bootstrap_build]
}
