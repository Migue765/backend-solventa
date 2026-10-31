import re
from pathlib import Path


def test_only_biometric_login_and_challenge_are_public_and_auth_throttled() -> None:
    root = Path(__file__).resolve().parents[1]
    terraform = (root / "terraform/modules/api-gateway/main.tf").read_text()
    routes = dict(
        re.findall(r'resource "aws_apigatewayv2_route" "([^"]+)" \{(.*?)\n\}', terraform, re.S)
    )
    expected = {
        "movil_biometric_challenge": "POST /movil/v1/biometria/desafios",
        "movil_biometric_login": "POST /movil/v1/sesiones/biometria",
    }
    biometric = {
        name: body
        for name, body in routes.items()
        if "/biometria/" in body or '/sesiones/biometria"' in body
    }
    assert biometric.keys() == expected.keys()
    stage = terraform.split('resource "aws_apigatewayv2_stage" "default" {')[1]
    settings = re.findall(r"  route_settings \{(.*?)\n  \}", stage, re.S)
    for name, route_key in expected.items():
        body = routes[name]
        assert f'route_key          = "{route_key}"' in body
        assert 'authorization_type = "NONE"' in body
        assert "authorizer_id" not in body
        assert 'target             = "integrations/${aws_apigatewayv2_integration.alb.id}"' in body
        matching = [item for item in settings if f"aws_apigatewayv2_route.{name}.route_key" in item]
        assert len(matching) == 1
        assert "throttling_rate_limit  = var.auth_throttling_rate_limit" in matching[0]
        assert "throttling_burst_limit = var.auth_throttling_burst_limit" in matching[0]
    for name, route_key in {"v1": "ANY /v1/{proxy+}", "movil": "ANY /movil/{proxy+}"}.items():
        assert f'route_key          = "{route_key}"' in routes[name]
        assert 'authorization_type = "JWT"' in routes[name]
        assert "authorizer_id      = aws_apigatewayv2_authorizer.jwt.id" in routes[name]
