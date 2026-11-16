mock_provider "aws" {
  override_during = plan
}

override_resource {
  target          = aws_security_group.db
  override_during = plan
  values          = { id = "sg-0123456789abcdef2" }
}

variables {
  name                       = "solventa-test"
  vpc_id                     = "vpc-0123456789abcdef0"
  subnet_ids                 = ["subnet-0123456789abcdef0", "subnet-0123456789abcdef1"]
  allowed_security_group_ids = ["sg-0123456789abcdef0"]
  kms_key_arn                = "arn:aws:kms:us-east-2:396383602233:key/00000000-0000-0000-0000-000000000001"
  database_name              = "solventa_test"
  identifier                 = "solventa-test"
}

run "legacy_group" {
  command = plan
  assert {
    condition = (
      length(aws_security_group.db) == 1 &&
      aws_db_instance.this.vpc_security_group_ids == toset([aws_security_group.db[0].id]) &&
      aws_vpc_security_group_ingress_rule.from_eks[0].from_port == 5432
    )
    error_message = "El consumidor legacy conserva su SG y puerto."
  }
}

run "precreated_group" {
  command = plan
  variables {
    existing_security_group_id = "sg-0123456789abcdef2"
  }
  assert {
    condition = (
      length(aws_security_group.db) == 0 &&
      output.security_group_id == var.existing_security_group_id &&
      aws_db_instance.this.vpc_security_group_ids == toset([var.existing_security_group_id]) &&
      aws_vpc_security_group_ingress_rule.from_eks[0].security_group_id == var.existing_security_group_id &&
      aws_vpc_security_group_ingress_rule.from_eks[0].referenced_security_group_id == var.allowed_security_group_ids[0]
    )
    error_message = "DB reutiliza el SG externo sin duplicarlo y conserva su regla desde EKS."
  }
}
