data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  account_id   = data.aws_caller_identity.current.account_id
  region       = data.aws_region.current.name
  pipeline     = "${var.name}-${var.service_name}"
  prefix       = coalesce(var.resource_prefix, var.name)
  service_dir  = coalesce(var.service_dir, "services/${var.service_name}")
  cluster_arn  = "arn:aws:eks:${local.region}:${local.account_id}:cluster/${var.cluster_name}"
  buildspec    = "deploy/codebuild"
  logs_prefix  = "logs"
  source_vars  = "SourceVariables"
  build_vars   = "BuildVariables"
  build_action = "Build"

  connection_arn     = coalesce(var.github_connection_arn, one(aws_codestarconnections_connection.github[*].arn))
  connection_service = split(":", local.connection_arn)[2]
  subnet_arns = [
    for subnet in var.private_subnet_ids : "arn:aws:ec2:${local.region}:${local.account_id}:subnet/${subnet}"
  ]
  vpc_arn = "arn:aws:ec2:${local.region}:${local.account_id}:vpc/${var.vpc_id}"
}

# ---------------------------------------------------------------------------
# Artefactos y logs (los logs van a S3: no se usa CloudWatch)
# ---------------------------------------------------------------------------
resource "aws_s3_bucket" "artifacts" {
  bucket        = "${local.prefix}-cicd-${local.account_id}"
  force_destroy = var.artifact_force_destroy

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
# Con github_connection_arn se reutiliza una conexion ya autorizada (otro servicio) y no se crea otra.
resource "aws_codestarconnections_connection" "github" {
  count = var.github_connection_arn == null ? 1 : 0

  name          = "${local.prefix}-github"
  provider_type = "GitHub"

  tags = var.tags
}

moved {
  from = aws_codestarconnections_connection.github
  to   = aws_codestarconnections_connection.github[0]
}

# ---------------------------------------------------------------------------
# Red del CodeBuild de despliegue
# ---------------------------------------------------------------------------
resource "aws_security_group" "deploy" {
  name        = "${local.prefix}-codebuild-deploy"
  description = var.restrict_service_access ? "CodeBuild de despliegue: HTTPS y paquetes HTTP, sin entrada" : "CodeBuild de despliegue: salida libre, sin entrada"
  vpc_id      = var.vpc_id

  tags = merge(var.tags, { Name = "${local.prefix}-codebuild-deploy" })
}

resource "aws_vpc_security_group_egress_rule" "deploy_all" {
  security_group_id = aws_security_group.deploy.id
  ip_protocol       = var.restrict_service_access ? "tcp" : "-1"
  from_port         = var.restrict_service_access ? 443 : null
  to_port           = var.restrict_service_access ? 443 : null
  cidr_ipv4         = "0.0.0.0/0"
  description       = "Salida a ECR, STS, EKS y descarga de kubectl"
}

resource "aws_vpc_security_group_egress_rule" "deploy_http" {
  count = var.restrict_service_access ? 1 : 0

  security_group_id = aws_security_group.deploy.id
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
  cidr_ipv4         = "0.0.0.0/0"
  description       = "Repositorios apt del fallback gettext-base en buildspec-deploy.yml"
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

data "aws_iam_policy_document" "codebuild_restricted_assume" {
  for_each = var.restrict_service_access ? toset(["build", "deploy"]) : toset([])

  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["codebuild.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }

    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = ["arn:aws:codebuild:${local.region}:${local.account_id}:project/${local.pipeline}-${each.key}"]
    }
  }
}

resource "aws_iam_role" "build" {
  name               = "${local.prefix}-codebuild-build"
  assume_role_policy = var.restrict_service_access ? data.aws_iam_policy_document.codebuild_restricted_assume["build"].json : data.aws_iam_policy_document.codebuild_assume.json

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
  name               = "${local.prefix}-codebuild-deploy"
  assume_role_policy = var.restrict_service_access ? data.aws_iam_policy_document.codebuild_restricted_assume["deploy"].json : data.aws_iam_policy_document.codebuild_assume.json

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

  dynamic "statement" {
    for_each = var.restrict_service_access ? [] : [1]

    content {
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
  }

  dynamic "statement" {
    for_each = var.restrict_service_access ? [1] : []

    content {
      sid = "DescribirRed"
      actions = [
        "ec2:DescribeDhcpOptions",
        "ec2:DescribeNetworkInterfaces",
        "ec2:DescribeSecurityGroups",
        "ec2:DescribeSubnets",
        "ec2:DescribeVpcs",
      ]
      resources = ["*"]
    }
  }

  dynamic "statement" {
    for_each = var.restrict_service_access ? [1] : []

    content {
      sid     = "CrearInterfaces"
      actions = ["ec2:CreateNetworkInterface"]
      # EC2 autoriza por separado la nueva ENI, cada SG y la subnet.
      resources = concat(
        ["arn:aws:ec2:${local.region}:${local.account_id}:network-interface/*", aws_security_group.deploy.arn],
        local.subnet_arns,
      )
    }
  }

  dynamic "statement" {
    for_each = var.restrict_service_access ? [1] : []

    content {
      sid       = "EliminarInterfaces"
      actions   = ["ec2:DeleteNetworkInterface"]
      resources = ["arn:aws:ec2:${local.region}:${local.account_id}:network-interface/*"]

      condition {
        test     = "ArnEquals"
        variable = "ec2:Vpc"
        values   = [local.vpc_arn]
      }

      condition {
        test     = "ArnEquals"
        variable = "ec2:Subnet"
        values   = local.subnet_arns
      }
    }
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

    dynamic "condition" {
      for_each = var.restrict_service_access ? {
        "ec2:Vpc"    = [local.vpc_arn]
        "ec2:Subnet" = local.subnet_arns
      } : {}

      content {
        test     = "ArnEquals"
        variable = condition.key
        values   = condition.value
      }
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
  description  = "Pruebas, construccion y publicacion de la imagen de ${var.service_name} en ECR"
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

    environment_variable {
      name  = "SERVICE_NAME"
      value = var.service_name
    }

    environment_variable {
      name  = "SERVICE_DIR"
      value = local.service_dir
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
  description  = "Migraciones y rollout de ${var.service_name} en EKS, con rollback automatico"
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

    environment_variable {
      name  = "SERVICE_NAME"
      value = var.service_name
    }

    # Solo se define cuando el servicio no migra (los demas proyectos quedan sin cambios).
    dynamic "environment_variable" {
      for_each = var.run_migrations ? [] : [1]

      content {
        name  = "MIGRATE"
        value = "false"
      }
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
  name               = "${local.prefix}-codepipeline"
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
    actions   = var.restrict_service_access ? ["${local.connection_service}:UseConnection"] : ["codestar-connections:UseConnection", "codeconnections:UseConnection"]
    resources = [local.connection_arn]

    dynamic "condition" {
      for_each = var.restrict_service_access ? [1] : []

      content {
        test     = "StringEquals"
        variable = "${local.connection_service}:FullRepositoryId"
        values   = [var.github_repository]
      }
    }

    # Las lecturas por SHA no incluyen necesariamente BranchName.
    dynamic "condition" {
      for_each = var.restrict_service_access ? [1] : []

      content {
        test     = "StringEqualsIfExists"
        variable = "${local.connection_service}:BranchName"
        values   = [var.branch_name]
      }
    }
  }

  statement {
    sid = "EjecutarBuilds"
    actions = var.restrict_service_access ? ["codebuild:BatchGetBuilds", "codebuild:StartBuild"] : [
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

  dynamic "trigger" {
    for_each = var.automatic_triggers_enabled ? [1] : []

    content {
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
        ConnectionArn        = local.connection_arn
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
