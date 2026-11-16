mock_provider "aws" {
  override_during = plan
}

override_resource {
  target          = aws_security_group.alb
  override_during = plan
  values          = { id = "sg-0123456789abcdef1" }
}

override_resource {
  target          = aws_security_group.vpc_link
  override_during = plan
  values          = { id = "sg-0123456789abcdef2" }
}

variables {
  name                     = "solventa-test"
  vpc_id                   = "vpc-0123456789abcdef0"
  private_subnet_ids       = ["subnet-0123456789abcdef0", "subnet-0123456789abcdef1"]
  target_security_group_id = "sg-0123456789abcdef0"
}

run "legacy_groups" {
  command = plan
  assert {
    condition = (
      length(aws_security_group.alb) == 1 &&
      length(aws_security_group.vpc_link) == 1 &&
      aws_lb.internal.security_groups == toset([aws_security_group.alb[0].id]) &&
      aws_apigatewayv2_vpc_link.this.security_group_ids == toset([aws_security_group.vpc_link[0].id]) &&
      aws_vpc_security_group_ingress_rule.alb_from_vpc_link.from_port == 80
    )
    error_message = "Los defaults conservan ambos SG y el listener HTTP."
  }
}

run "precreated_groups_https" {
  command = plan
  variables {
    existing_security_group_ids = {
      alb      = "sg-0123456789abcdef1"
      vpc_link = "sg-0123456789abcdef2"
    }
    listener_protocol = "HTTPS"
    certificate_arn   = "arn:aws:acm:us-east-2:396383602233:certificate/00000000-0000-0000-0000-000000000001"
    tls_server_name   = "test.invalid"
  }
  assert {
    condition = (
      length(aws_security_group.alb) == 0 &&
      length(aws_security_group.vpc_link) == 0 &&
      aws_lb.internal.security_groups == toset([var.existing_security_group_ids.alb]) &&
      aws_apigatewayv2_vpc_link.this.security_group_ids == toset([var.existing_security_group_ids.vpc_link]) &&
      aws_vpc_security_group_egress_rule.vpc_link_to_alb.security_group_id == var.existing_security_group_ids.vpc_link &&
      aws_vpc_security_group_ingress_rule.alb_from_vpc_link.security_group_id == var.existing_security_group_ids.alb &&
      aws_vpc_security_group_egress_rule.alb_to_targets.referenced_security_group_id == var.target_security_group_id &&
      aws_vpc_security_group_ingress_rule.targets_from_alb.referenced_security_group_id == var.existing_security_group_ids.alb &&
      aws_vpc_security_group_ingress_rule.alb_from_vpc_link.from_port == 443 &&
      aws_lb_listener.this.protocol == "HTTPS"
    )
    error_message = "Reutilizar exactamente los dos SG, conservar enlaces EKS y permitir HTTPS sin duplicaciones."
  }
}
