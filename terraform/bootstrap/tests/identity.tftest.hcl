mock_provider "aws" {
  mock_data "aws_iam_policy_document" {
    defaults = {
      json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}"
    }
  }

  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "396383602233"
      arn        = "arn:aws:sts::396383602233:assumed-role/test-bootstrap/session"
      user_id    = "synthetic-session"
    }
  }
}

variables {
  account_id  = "396383602233"
  aws_profile = "synthetic-role-profile"
}

run "assumed_role" {
  command = plan
}

run "reject_root" {
  command = plan
  override_data {
    target = data.aws_caller_identity.current
    values = {
      arn = "arn:aws:iam::396383602233:root"
    }
  }
  expect_failures = [data.aws_caller_identity.current]
}

run "reject_iam_user" {
  command = plan
  override_data {
    target = data.aws_caller_identity.current
    values = {
      arn = "arn:aws:iam::396383602233:user/synthetic-test"
    }
  }
  expect_failures = [data.aws_caller_identity.current]
}

run "reject_wrong_account_role" {
  command = plan
  override_data {
    target = data.aws_caller_identity.current
    values = {
      arn = "arn:aws:sts::123456789012:assumed-role/test-bootstrap/session"
    }
  }
  expect_failures = [data.aws_caller_identity.current]
}
