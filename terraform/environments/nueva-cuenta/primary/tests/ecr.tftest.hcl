mock_provider "aws" {}
mock_provider "tls" {}
mock_provider "random" {}

variables {
  account_id          = "096571798698"
  aws_profile         = "synthetic-role-profile"
  deployment_role_arn = "arn:aws:iam::096571798698:role/test-deploy"
  pipeline_services   = []
}

run "one_private_repository_per_api" {
  command = plan

  assert {
    condition = toset(keys(output.ecr_repositories)) == toset([
      "asistencias", "bff-movil", "bff-web", "cotizacion", "identidad",
      "notificaciones", "perfilamiento", "polizas", "siniestros",
    ])
    error_message = "Deben declararse exactamente las nueve APIs, sin omisiones ni repositorio comun."
  }

  assert {
    condition = alltrue([
      for service, repository in output.ecr_repositories : repository.name == "solventa/${service}"
    ])
    error_message = "Cada API debe publicar exclusivamente en su repositorio solventa/<api>."
  }
}

run "role_with_iam_path" {
  command = plan
  variables {
    deployment_role_arn = "arn:aws:iam::096571798698:role/infra/test-deploy"
  }
}

run "reject_previous_account" {
  command = plan
  variables {
    account_id = "396383602233"
  }
  expect_failures = [var.account_id]
}

run "reject_other_region" {
  command = plan
  variables {
    aws_region = "us-east-1"
  }
  expect_failures = [var.aws_region]
}

run "reject_empty_profile" {
  command = plan
  variables {
    aws_profile = ""
  }
  expect_failures = [var.aws_profile]
}

run "reject_user_as_expected_role" {
  command = plan
  variables {
    deployment_role_arn = "arn:aws:iam::096571798698:user/admin"
  }
  expect_failures = [var.deployment_role_arn]
}

run "reject_other_account_role_configuration" {
  command = plan
  variables {
    deployment_role_arn = "arn:aws:iam::123456789012:role/test-deploy"
  }
  expect_failures = [var.deployment_role_arn]
}

run "reject_direct_admin_user" {
  command = plan
  override_data {
    target = data.aws_caller_identity.deployment
    values = {
      arn = "arn:aws:iam::096571798698:user/admin"
    }
  }
  expect_failures = [data.aws_caller_identity.deployment]
}

run "reject_root" {
  command = plan
  override_data {
    target = data.aws_caller_identity.deployment
    values = {
      arn = "arn:aws:iam::096571798698:root"
    }
  }
  expect_failures = [data.aws_caller_identity.deployment]
}

run "reject_unapproved_role" {
  command = plan
  override_data {
    target = data.aws_caller_identity.deployment
    values = {
      arn = "arn:aws:sts::096571798698:assumed-role/other-role/session"
    }
  }
  expect_failures = [data.aws_caller_identity.deployment]
}

run "reject_wrong_caller_account" {
  command = plan
  override_data {
    target = data.aws_caller_identity.deployment
    values = {
      account_id = "123456789012"
      arn        = "arn:aws:sts::123456789012:assumed-role/test-deploy/session"
    }
  }
  expect_failures = [data.aws_caller_identity.deployment]
}
