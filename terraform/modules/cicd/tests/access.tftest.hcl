provider "aws" {
  region                      = "us-east-2"
  access_key                  = "testing"
  secret_key                  = "testing"
  skip_credentials_validation = true
  skip_requesting_account_id  = true
  skip_metadata_api_check     = true
}

override_data {
  target = data.aws_caller_identity.current
  values = { account_id = "111122223333" }
}

override_data {
  target = data.aws_region.current
  values = { name = "us-east-2" }
}

override_resource {
  target          = aws_s3_bucket.artifacts
  override_during = plan
  values = {
    id  = "solventa-prod-perfilamiento-cicd-111122223333"
    arn = "arn:aws:s3:::solventa-prod-perfilamiento-cicd-111122223333"
  }
}

override_resource {
  target          = aws_security_group.deploy
  override_during = plan
  values = {
    id  = "sg-0123456789abcdef0"
    arn = "arn:aws:ec2:us-east-2:111122223333:security-group/sg-0123456789abcdef0"
  }
}

override_resource {
  target          = aws_codebuild_project.build
  override_during = plan
  values = {
    arn = "arn:aws:codebuild:us-east-2:111122223333:project/solventa-prod-perfilamiento-build"
  }
}

override_resource {
  target          = aws_codebuild_project.deploy
  override_during = plan
  values = {
    arn = "arn:aws:codebuild:us-east-2:111122223333:project/solventa-prod-perfilamiento-deploy"
  }
}

variables {
  name                      = "solventa-prod"
  service_name              = "perfilamiento"
  resource_prefix           = "solventa-prod-perfilamiento"
  github_connection_arn     = "arn:aws:codestar-connections:us-east-2:111122223333:connection/00000000-0000-0000-0000-000000000001"
  github_repository         = "Migue765/backend-solventa"
  vpc_id                    = "vpc-0123456789abcdef0"
  private_subnet_ids        = ["subnet-0123456789abcdef0", "subnet-0123456789abcdef1"]
  cluster_name              = "solventa-prod-eks"
  cluster_security_group_id = "sg-0123456789abcdef1"
  service_namespace         = "perfilamiento"
  ecr_repository_arn        = "arn:aws:ecr:us-east-2:111122223333:repository/solventa/perfilamiento"
  ecr_repository_url        = "111122223333.dkr.ecr.us-east-2.amazonaws.com/solventa/perfilamiento"
}

run "legacy_defaults" {
  command = plan

  assert {
    condition = (
      aws_s3_bucket.artifacts.force_destroy &&
      aws_vpc_security_group_egress_rule.deploy_all.ip_protocol == "-1" &&
      aws_vpc_security_group_egress_rule.deploy_all.from_port == null &&
      aws_vpc_security_group_egress_rule.deploy_all.to_port == null &&
      length(aws_vpc_security_group_egress_rule.deploy_http) == 0 &&
      length(data.aws_iam_policy_document.codebuild_restricted_assume) == 0
    )
    error_message = "Los defaults deben conservar bucket, direcciones y red de los consumidores existentes."
  }

  assert {
    condition = (
      jsondecode(aws_iam_role.build.assume_role_policy) == jsondecode(data.aws_iam_policy_document.codebuild_assume.json) &&
      jsondecode(aws_iam_role.deploy.assume_role_policy) == jsondecode(data.aws_iam_policy_document.codebuild_assume.json) &&
      toset(one([for s in data.aws_iam_policy_document.deploy.statement : s if s.sid == "RedVpc"]).resources) == toset(["*"]) &&
      length(one([for s in data.aws_iam_policy_document.pipeline.statement : s if s.sid == "ConexionGitHub"]).condition) == 0
    )
    error_message = "Los defaults deben conservar el trust y los permisos previos."
  }
}

run "restricted_access" {
  command = plan

  variables {
    restrict_service_access    = true
    artifact_force_destroy     = false
    require_manual_approval    = true
    automatic_triggers_enabled = false
  }

  assert {
    condition = alltrue([
      for phase in ["build", "deploy"] : (
        jsondecode(data.aws_iam_policy_document.codebuild_restricted_assume[phase].json).Statement[0].Condition.StringEquals["aws:SourceAccount"] == "111122223333" &&
        jsondecode(data.aws_iam_policy_document.codebuild_restricted_assume[phase].json).Statement[0].Condition.ArnEquals["aws:SourceArn"] == "arn:aws:codebuild:us-east-2:111122223333:project/solventa-prod-perfilamiento-${phase}"
      )
    ])
    error_message = "Cada trust CodeBuild debe aceptar solo su cuenta y proyecto exactos."
  }

  assert {
    condition = (
      !aws_s3_bucket.artifacts.force_destroy &&
      aws_vpc_security_group_egress_rule.deploy_all.ip_protocol == "tcp" &&
      aws_vpc_security_group_egress_rule.deploy_all.from_port == 443 &&
      aws_vpc_security_group_egress_rule.deploy_all.to_port == 443 &&
      aws_vpc_security_group_egress_rule.deploy_http[0].from_port == 80 &&
      aws_vpc_security_group_egress_rule.deploy_http[0].to_port == 80 &&
      aws_vpc_security_group_ingress_rule.cluster_from_deploy.from_port == 443 &&
      aws_vpc_security_group_ingress_rule.cluster_from_deploy.referenced_security_group_id == aws_security_group.deploy.id
    )
    error_message = "La instancia restringida debe proteger S3 y abrir solo TCP 443/80, sin entrada al build."
  }

  assert {
    condition = (
      toset(one([for s in data.aws_iam_policy_document.deploy.statement : s if s.sid == "CrearInterfaces"]).resources) == toset(concat(
        ["arn:aws:ec2:us-east-2:111122223333:network-interface/*", aws_security_group.deploy.arn],
        local.subnet_arns,
      )) &&
      toset(one([for s in data.aws_iam_policy_document.deploy.statement : s if s.sid == "EliminarInterfaces"]).resources) == toset(["arn:aws:ec2:us-east-2:111122223333:network-interface/*"]) &&
      toset(one([for s in one([for s in data.aws_iam_policy_document.deploy.statement : s if s.sid == "EliminarInterfaces"]).condition : s if s.variable == "ec2:Vpc"]).values) == toset([local.vpc_arn]) &&
      toset(one([for s in one([for s in data.aws_iam_policy_document.deploy.statement : s if s.sid == "RedVpcPermiso"]).condition : s if s.variable == "ec2:Subnet"]).values) == toset(local.subnet_arns) &&
      alltrue([for s in data.aws_iam_policy_document.deploy.statement : alltrue([for a in s.actions : startswith(a, "ec2:Describe")]) if contains(s.resources, "*")])
    )
    error_message = "Las mutaciones ENI deben limitar recursos, VPC y subnets; solo Describe puede usar Resource *."
  }

  assert {
    condition = (
      toset(one([for s in data.aws_iam_policy_document.pipeline.statement : s if s.sid == "ConexionGitHub"]).actions) == toset(["codestar-connections:UseConnection"]) &&
      one([for c in one([for s in data.aws_iam_policy_document.pipeline.statement : s if s.sid == "ConexionGitHub"]).condition : c if c.variable == "codestar-connections:FullRepositoryId"]).test == "StringEquals" &&
      toset(one([for c in one([for s in data.aws_iam_policy_document.pipeline.statement : s if s.sid == "ConexionGitHub"]).condition : c if c.variable == "codestar-connections:FullRepositoryId"]).values) == toset(["Migue765/backend-solventa"]) &&
      one([for c in one([for s in data.aws_iam_policy_document.pipeline.statement : s if s.sid == "ConexionGitHub"]).condition : c if c.variable == "codestar-connections:BranchName"]).test == "StringEqualsIfExists" &&
      toset(one([for c in one([for s in data.aws_iam_policy_document.pipeline.statement : s if s.sid == "ConexionGitHub"]).condition : c if c.variable == "codestar-connections:BranchName"]).values) == toset(["main"]) &&
      length(one(data.aws_iam_policy_document.pipeline_assume.statement).condition) == 0
    )
    error_message = "Source debe acotar conexion/repositorio y la rama cuando exista en el contexto, sin inventar trust de CodePipeline."
  }

  assert {
    condition = (
      aws_eks_access_policy_association.deploy.access_scope[0].namespaces == toset(["perfilamiento"]) &&
      length(aws_codepipeline.this.trigger) == 0 &&
      contains([for s in aws_codepipeline.this.stage : s.name], "Approve") &&
      alltrue(flatten([
        for p in [data.aws_iam_policy_document.build, data.aws_iam_policy_document.deploy, data.aws_iam_policy_document.pipeline] : [
          for s in p.statement : [for a in s.actions : !startswith(a, "iam:")]
        ]
      ]))
    )
    error_message = "Conservar namespace propio, aprobacion, disparadores pausados y ausencia de permisos IAM/PassRole."
  }
}
