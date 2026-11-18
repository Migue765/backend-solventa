mock_provider "aws" {
  override_during = plan
}

override_resource {
  target          = aws_apigatewayv2_api.this
  override_during = plan
  values = {
    id           = "synthetic-api"
    api_endpoint = "https://synthetic.execute-api.us-east-2.amazonaws.com"
  }
}

override_resource {
  target          = aws_apigatewayv2_integration.alb
  override_during = plan
  values = {
    id = "synthetic-integration"
  }
}

override_resource {
  target          = aws_apigatewayv2_authorizer.jwt
  override_during = plan
  values = {
    id = "synthetic-jwt"
  }
}

variables {
  name                     = "solventa-test"
  vpc_id                   = "vpc-0123456789abcdef0"
  private_subnet_ids       = ["subnet-0123456789abcdef0", "subnet-0123456789abcdef1"]
  target_security_group_id = "sg-0123456789abcdef0"
}

run "bootstrap_without_protected_routes" {
  command = plan
  variables {
    jwt_routes_enabled = false
  }

  assert {
    condition = (
      length(aws_apigatewayv2_authorizer.jwt) == 0 &&
      length(aws_apigatewayv2_route.v1) == 0 &&
      length(aws_apigatewayv2_route.movil) == 0
    )
    error_message = "El arranque no debe crear authorizer ni publicar proxies sin JWT."
  }

  assert {
    condition = (
      aws_apigatewayv2_route.oidc_discovery.route_key == "GET /.well-known/openid-configuration" &&
      aws_apigatewayv2_route.jwks.route_key == "GET /.well-known/jwks.json" &&
      aws_lb_listener_rule.identidad.priority == 30 &&
      output.issuer == aws_apigatewayv2_api.this.api_endpoint &&
      aws_apigatewayv2_stage.default.auto_deploy
    )
    error_message = "Discovery/JWKS y el issuer real deben poder arrancar antes del authorizer."
  }
}

run "enable_protected_routes_after_discovery" {
  command = plan

  assert {
    condition = (
      var.jwt_routes_enabled &&
      length(aws_apigatewayv2_authorizer.jwt) == 1 &&
      length(aws_apigatewayv2_route.v1) == 1 &&
      length(aws_apigatewayv2_route.movil) == 1 &&
      aws_apigatewayv2_route.v1[0].authorization_type == "JWT" &&
      aws_apigatewayv2_route.movil[0].authorization_type == "JWT" &&
      aws_apigatewayv2_route.v1[0].authorizer_id == aws_apigatewayv2_authorizer.jwt[0].id &&
      aws_apigatewayv2_route.movil[0].authorizer_id == aws_apigatewayv2_authorizer.jwt[0].id &&
      one(aws_apigatewayv2_authorizer.jwt[0].jwt_configuration).issuer == run.bootstrap_without_protected_routes.issuer &&
      toset(one(aws_apigatewayv2_authorizer.jwt[0].jwt_configuration).audience) == toset([var.jwt_audience])
    )
    error_message = "El default debe activar ambos proxies con el mismo issuer/audience y JWT obligatorio."
  }
}
