mock_provider "aws" {}

override_resource {
  target          = aws_lb_target_group.bff_web
  override_during = plan
  values = {
    arn = "arn:aws:elasticloadbalancing:us-east-2:123456789012:targetgroup/bff-web/0123456789abcdef"
  }
}

override_resource {
  target          = aws_apigatewayv2_integration.alb
  override_during = plan
  values = {
    id = "test-alb-integration"
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

run "preflight_routes_do_not_require_jwt" {
  command = plan

  assert {
    condition = (
      aws_apigatewayv2_route.v1_options.route_key == "OPTIONS /v1/{proxy+}" &&
      aws_apigatewayv2_route.v1_options.authorization_type == "NONE" &&
      aws_apigatewayv2_route.v1_options.target == "integrations/${aws_apigatewayv2_integration.alb.id}" &&
      aws_apigatewayv2_route.v1.authorization_type == "JWT"
    )
    error_message = "El preflight web debe ser publico sin quitar JWT de las demas solicitudes."
  }

  assert {
    condition = (
      aws_apigatewayv2_route.movil_options.route_key == "OPTIONS /movil/{proxy+}" &&
      aws_apigatewayv2_route.movil_options.authorization_type == "NONE" &&
      aws_apigatewayv2_route.movil_options.target == "integrations/${aws_apigatewayv2_integration.alb.id}" &&
      aws_apigatewayv2_route.movil.authorization_type == "JWT"
    )
    error_message = "El preflight movil debe ser publico sin quitar JWT de las demas solicitudes."
  }
}

run "evidence_preflight_keeps_gateway_security" {
  command = plan

  assert {
    condition = toset(one(aws_apigatewayv2_api.this.cors_configuration).allow_headers) == toset([
      "authorization", "content-type", "idempotency-key", "x-correlation-id", "api-version", "upload-offset"
    ])
    error_message = "El preflight de carga debe permitir Upload-Offset sin ampliar los demas encabezados."
  }

  assert {
    condition     = toset(one(aws_apigatewayv2_api.this.cors_configuration).allow_methods) == toset(["GET", "POST", "PUT", "DELETE", "OPTIONS"])
    error_message = "El preflight de carga debe conservar los metodos autorizados."
  }

  assert {
    condition     = toset(one(aws_apigatewayv2_api.this.cors_configuration).allow_origins) == toset(["https://jazmincorandes.github.io", "http://localhost:4200"]) && one(aws_apigatewayv2_api.this.cors_configuration).allow_credentials == false
    error_message = "CORS debe conservar los origenes existentes sin credenciales ni comodines."
  }

  assert {
    condition     = toset(one(aws_apigatewayv2_api.this.cors_configuration).expose_headers) == toset(["x-correlation-id", "retry-after", "date", "api-version", "location"])
    error_message = "La carga no requiere exponer encabezados adicionales de respuesta."
  }

  assert {
    condition     = aws_apigatewayv2_route.v1.authorization_type == "JWT" && aws_apigatewayv2_route.movil.authorization_type == "JWT"
    error_message = "Las rutas web y movil deben seguir protegidas por JWT."
  }
}
