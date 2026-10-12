data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  account_id   = data.aws_caller_identity.current.account_id
  region       = data.aws_region.current.name
  pipeline     = "${var.name}-cotizacion"
  cluster_arn  = "arn:aws:eks:${local.region}:${local.account_id}:cluster/${var.cluster_name}"
  buildspec    = "deploy/codebuild"
  logs_prefix  = "logs"
  source_vars  = "SourceVariables"
  build_vars   = "BuildVariables"
  build_action = "Build"
}

# ---------------------------------------------------------------------------
# Artefactos y logs (los logs van a S3: no se usa CloudWatch)
# ---------------------------------------------------------------------------
resource "aws_s3_bucket" "artifacts" {
  bucket        = "${var.name}-cicd-${local.account_id}"
  force_destroy = true

  tags = var.tags
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  rule {
    id     = "expire"
    status = "Enabled"

    filter {}

    expiration {
      days = var.artifact_retention_days
    }
  }
}

# ---------------------------------------------------------------------------
# Conexion con GitHub (queda PENDING hasta autorizarla una vez en la consola)
# ---------------------------------------------------------------------------
resource "aws_codestarconnections_connection" "github" {
  name          = "${var.name}-github"
  provider_type = "GitHub"

  tags = var.tags
}

# ---------------------------------------------------------------------------
# Red del CodeBuild de despliegue
# ---------------------------------------------------------------------------
resource "aws_security_group" "deploy" {
  name        = "${var.name}-codebuild-deploy"
  description = "CodeBuild de despliegue: salida libre, sin entrada"
  vpc_id      = var.vpc_id

  tags = merge(var.tags, { Name = "${var.name}-codebuild-deploy" })
}

resource "aws_vpc_security_group_egress_rule" "deploy_all" {
  security_group_id = aws_security_group.deploy.id
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
  description       = "Salida a ECR, STS, EKS y descarga de kubectl"
}

resource "aws_vpc_security_group_ingress_rule" "cluster_from_deploy" {
  security_group_id            = var.cluster_security_group_id
  referenced_security_group_id = aws_security_group.deploy.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  description                  = "API server de EKS desde CodeBuild de despliegue"
}

# ---------------------------------------------------------------------------
# IAM: CodeBuild (build)
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "codebuild_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["codebuild.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "build" {
  name               = "${var.name}-codebuild-build"
  assume_role_policy = data.aws_iam_policy_document.codebuild_assume.json

  tags = var.tags
}

data "aws_iam_policy_document" "build" {
  statement {
    sid       = "Artefactos"
    actions   = ["s3:GetObject", "s3:GetObjectVersion", "s3:PutObject", "s3:GetBucketLocation"]
    resources = [aws_s3_bucket.artifacts.arn, "${aws_s3_bucket.artifacts.arn}/*"]
  }

  statement {
    sid       = "EcrToken"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid = "EcrPublicar"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:CompleteLayerUpload",
      "ecr:DescribeImages",
      "ecr:GetDownloadUrlForLayer",
      "ecr:InitiateLayerUpload",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
    ]
    resources = [var.ecr_repository_arn]
  }
}

resource "aws_iam_role_policy" "build" {
  name   = "build"
  role   = aws_iam_role.build.id
  policy = data.aws_iam_policy_document.build.json
}

# ---------------------------------------------------------------------------
# IAM: CodeBuild (despliegue)
# ---------------------------------------------------------------------------
resource "aws_iam_role" "deploy" {
  name               = "${var.name}-codebuild-deploy"
  assume_role_policy = data.aws_iam_policy_document.codebuild_assume.json

  tags = var.tags
}

data "aws_iam_policy_document" "deploy" {
  statement {
    sid       = "Artefactos"
    actions   = ["s3:GetObject", "s3:GetObjectVersion", "s3:PutObject", "s3:GetBucketLocation"]
    resources = [aws_s3_bucket.artifacts.arn, "${aws_s3_bucket.artifacts.arn}/*"]
  }

  statement {
    sid       = "DescribirCluster"
    actions   = ["eks:DescribeCluster"]
    resources = [local.cluster_arn]
  }

  statement {
    sid = "RedVpc"
    actions = [
      "ec2:CreateNetworkInterface",
      "ec2:DeleteNetworkInterface",
      "ec2:DescribeDhcpOptions",
      "ec2:DescribeNetworkInterfaces",
      "ec2:DescribeSecurityGroups",
      "ec2:DescribeSubnets",
      "ec2:DescribeVpcs",
    ]
    resources = ["*"]
  }

  statement {
    sid       = "RedVpcPermiso"
    actions   = ["ec2:CreateNetworkInterfacePermission"]
    resources = ["arn:aws:ec2:${local.region}:${local.account_id}:network-interface/*"]

    condition {
      test     = "StringEquals"
      variable = "ec2:AuthorizedService"
      values   = ["codebuild.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "deploy" {
  name   = "deploy"
  role   = aws_iam_role.deploy.id
  policy = data.aws_iam_policy_document.deploy.json
}

# Acceso a Kubernetes limitado al namespace del servicio
resource "aws_eks_access_entry" "deploy" {
  cluster_name  = var.cluster_name
  principal_arn = aws_iam_role.deploy.arn
  type          = "STANDARD"

  tags = var.tags
}

resource "aws_eks_access_policy_association" "deploy" {
  cluster_name  = var.cluster_name
  principal_arn = aws_iam_role.deploy.arn
  policy_arn    = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSEditPolicy"

  access_scope {
    type       = "namespace"
    namespaces = [var.service_namespace]
  }

  depends_on = [aws_eks_access_entry.deploy]
}

# ---------------------------------------------------------------------------
# Proyectos CodeBuild
# ---------------------------------------------------------------------------
resource "aws_codebuild_project" "build" {
  name         = "${local.pipeline}-build"
  description  = "Pruebas, construccion y publicacion de la imagen de cotizacion en ECR"
  service_role = aws_iam_role.build.arn

  artifacts {
    type = "CODEPIPELINE"
  }

  environment {
    compute_type    = var.compute_type
    image           = "aws/codebuild/standard:7.0"
    type            = "LINUX_CONTAINER"
    privileged_mode = true

    environment_variable {
      name  = "ECR_REPOSITORY_URL"
      value = var.ecr_repository_url
    }
  }

  source {
    type      = "CODEPIPELINE"
    buildspec = "${local.buildspec}/buildspec-build.yml"
  }

  logs_config {
    cloudwatch_logs {
      status = "DISABLED"
    }

    s3_logs {
      status   = "ENABLED"
      location = "${aws_s3_bucket.artifacts.id}/${local.logs_prefix}/build"
    }
  }

  tags = var.tags
}

resource "aws_codebuild_project" "deploy" {
  name         = "${local.pipeline}-deploy"
  description  = "Migraciones y rollout de cotizacion en EKS, con rollback automatico"
  service_role = aws_iam_role.deploy.arn

  artifacts {
    type = "CODEPIPELINE"
  }

  environment {
    compute_type = var.compute_type
    image        = "aws/codebuild/standard:7.0"
    type         = "LINUX_CONTAINER"

    environment_variable {
      name  = "CLUSTER_NAME"
      value = var.cluster_name
    }

    environment_variable {
      name  = "NAMESPACE"
      value = var.service_namespace
    }

    environment_variable {
      name  = "ECR_REPOSITORY_URL"
      value = var.ecr_repository_url
    }
  }

  source {
    type      = "CODEPIPELINE"
    buildspec = "${local.buildspec}/buildspec-deploy.yml"
  }

  vpc_config {
    vpc_id             = var.vpc_id
    subnets            = var.private_subnet_ids
    security_group_ids = [aws_security_group.deploy.id]
  }

  logs_config {
    cloudwatch_logs {
      status = "DISABLED"
    }

    s3_logs {
      status   = "ENABLED"
      location = "${aws_s3_bucket.artifacts.id}/${local.logs_prefix}/deploy"
    }
  }

  tags = var.tags

  depends_on = [aws_eks_access_policy_association.deploy]
}

# ---------------------------------------------------------------------------
# IAM: CodePipeline
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "pipeline_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["codepipeline.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "pipeline" {
  name               = "${var.name}-codepipeline"
  assume_role_policy = data.aws_iam_policy_document.pipeline_assume.json

  tags = var.tags
}

data "aws_iam_policy_document" "pipeline" {
  statement {
    sid = "Artefactos"
    actions = [
      "s3:GetBucketVersioning",
      "s3:GetObject",
      "s3:GetObjectVersion",
      "s3:PutObject",
      "s3:GetBucketLocation",
    ]
    resources = [aws_s3_bucket.artifacts.arn, "${aws_s3_bucket.artifacts.arn}/*"]
  }

  statement {
    sid       = "ConexionGitHub"
    actions   = ["codestar-connections:UseConnection", "codeconnections:UseConnection"]
    resources = [aws_codestarconnections_connection.github.arn]
  }

  statement {
    sid = "EjecutarBuilds"
    actions = [
      "codebuild:BatchGetBuilds",
      "codebuild:StartBuild",
      "codebuild:BatchGetBuildBatches",
      "codebuild:StartBuildBatch",
    ]
    resources = [aws_codebuild_project.build.arn, aws_codebuild_project.deploy.arn]
  }
}

resource "aws_iam_role_policy" "pipeline" {
  name   = "pipeline"
  role   = aws_iam_role.pipeline.id
  policy = data.aws_iam_policy_document.pipeline.json
}

# ---------------------------------------------------------------------------
# Pipeline: tag de version -> build -> (aprobacion) -> migracion + rollout
# ---------------------------------------------------------------------------
resource "aws_codepipeline" "this" {
  name           = local.pipeline
  role_arn       = aws_iam_role.pipeline.arn
  pipeline_type  = "V2"
  execution_mode = "QUEUED"

  artifact_store {
    type     = "S3"
    location = aws_s3_bucket.artifacts.bucket
  }

  trigger {
    provider_type = "CodeStarSourceConnection"

    git_configuration {
      source_action_name = "Source"

      push {
        tags {
          includes = var.release_tag_patterns
        }
      }
    }
  }

  stage {
    name = "Source"

    action {
      name             = "Source"
      category         = "Source"
      owner            = "AWS"
      provider         = "CodeStarSourceConnection"
      version          = "1"
      namespace        = local.source_vars
      output_artifacts = ["source"]

      configuration = {
        ConnectionArn        = aws_codestarconnections_connection.github.arn
        FullRepositoryId     = var.github_repository
        BranchName           = var.branch_name
        OutputArtifactFormat = "CODE_ZIP"
        DetectChanges        = "false"
      }
    }
  }

  stage {
    name = "Build"

    action {
      name             = local.build_action
      category         = "Build"
      owner            = "AWS"
      provider         = "CodeBuild"
      version          = "1"
      namespace        = local.build_vars
      input_artifacts  = ["source"]
      output_artifacts = ["build"]

      configuration = {
        ProjectName = aws_codebuild_project.build.name
        EnvironmentVariables = jsonencode([
          { name = "COMMIT_ID", value = "#{${local.source_vars}.CommitId}", type = "PLAINTEXT" },
        ])
      }
    }
  }

  dynamic "stage" {
    for_each = var.require_manual_approval ? [1] : []

    content {
      name = "Approve"

      action {
        name     = "Approve"
        category = "Approval"
        owner    = "AWS"
        provider = "Manual"
        version  = "1"

        configuration = {
          CustomData = "Aprobar el despliegue de la imagen #{${local.build_vars}.IMAGE_TAG} a produccion"
        }
      }
    }
  }

  stage {
    name = "Deploy"

    action {
      name            = "Deploy"
      category        = "Build"
      owner           = "AWS"
      provider        = "CodeBuild"
      version         = "1"
      input_artifacts = ["source"]

      configuration = {
        ProjectName = aws_codebuild_project.deploy.name
        EnvironmentVariables = jsonencode([
          { name = "IMAGE_TAG", value = "#{${local.build_vars}.IMAGE_TAG}", type = "PLAINTEXT" },
        ])
      }
    }
  }

  tags = var.tags
}
