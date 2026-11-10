mock_provider "aws" {}

override_resource {
  target          = aws_lb_target_group.bff_web
  override_during = plan
  values = {
    arn = "arn:aws:elasticloadbalancing:us-east-2:123456789012:targetgroup/bff-web/0123456789abcdef"
  }
}

variables {
  name                     = "solventa-test"
  vpc_id                   = "vpc-00000000000000000"
  private_subnet_ids       = ["subnet-00000000000000001", "subnet-00000000000000002"]
  target_security_group_id = "sg-00000000000000000"
}

run "destinations_reach_bff_web" {
  command = plan

  assert {
    condition     = one(one(aws_lb_listener_rule.bff_web_destinations.condition).path_pattern).values == toset(["/v1/destinos-viaje"])
    error_message = "Los destinos de viaje deben tener una ruta exacta independiente."
  }

  assert {
    condition     = one(aws_lb_listener_rule.bff_web_destinations.action).target_group_arn == aws_lb_target_group.bff_web.arn
    error_message = "Los destinos de viaje deben enviarse al BFF web, no a cotizacion."
  }

  assert {
    condition     = aws_lb_listener_rule.bff_web_destinations.priority == 11 && aws_lb_listener_rule.bff_web.priority == 10 && length(var.bff_web_path_patterns) == 5
    error_message = "La nueva ruta no debe desplazar la regla web ni exceder sus cinco patrones."
  }
}
