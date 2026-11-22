mock_provider "aws" {
  override_during = plan
  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "396383602233"
      arn        = "arn:aws:sts::396383602233:assumed-role/test-network/session"
    }
  }
  mock_data "aws_availability_zones" {
    defaults = { names = ["us-east-2a", "us-east-2b"] }
  }
  mock_data "aws_region" {
    defaults = { name = "us-east-2" }
  }
}

variables {
  account_id  = "396383602233"
  aws_profile = "test-only"
}

run "network_and_thirteen_groups" {
  command = plan
  assert {
    condition = (
      length(module.network.public_subnet_ids) == 2 &&
      length(module.network.private_subnet_ids) == 2 &&
      length(aws_security_group.cicd) == 9 &&
      aws_security_group.cicd["cotizacion"].name == "solventa-prod-codebuild-deploy" &&
      aws_security_group.cicd["perfilamiento"].description == "CodeBuild de despliegue: HTTPS y paquetes HTTP, sin entrada" &&
      aws_security_group.cicd["bff-web"].description == "CodeBuild de despliegue: salida libre, sin entrada" &&
      aws_security_group.db.name == "solventa-prod-db" &&
      aws_security_group.redis.name == "solventa-prod-redis-sg" &&
      aws_security_group.alb.name == "solventa-prod-alb-internal" &&
      aws_security_group.vpc_link.name == "solventa-prod-vpclink" &&
      output.network_context.account_id == "396383602233"
    )
    error_message = "Conservar la red declarada, los trece SG y la distincion de Perfilamiento, sin workloads."
  }
}

run "reject_other_account" {
  command = plan
  variables {
    account_id = "111122223333"
  }
  expect_failures = [var.account_id]
}

run "reject_root" {
  command = plan
  override_data {
    target = data.aws_caller_identity.deployment
    values = {
      account_id = "396383602233"
      arn        = "arn:aws:iam::396383602233:root"
    }
  }
  expect_failures = [data.aws_caller_identity.deployment]
}
