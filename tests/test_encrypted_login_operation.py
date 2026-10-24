import argparse
import json
import subprocess
from types import SimpleNamespace
from typing import Any

import fakeredis
import pytest
import yaml
from jwcrypto import jwk

from scripts.configure_encrypted_login import IDENTITY_CHECK, configure
from tests.test_academic_infrastructure import K8S, ROOT


def test_identity_preflight_executes_jose_and_cleans_its_challenge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = jwk.JWK.generate(kty="RSA", size=2048)
    signer = jwk.JWK.generate(kty="RSA", size=2048)
    config = SimpleNamespace(
        redis_url="redis://unused.test",
        login_encryption_private_key=key.export_to_pem(private_key=True, password=None).decode(),
        jwt_private_key=signer.export_to_pem().decode(),
    )
    client = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr("identidad.config.Config.from_environment", lambda: config)
    monkeypatch.setattr("redis.Redis.from_url", lambda *args, **kwargs: client)
    exec(IDENTITY_CHECK, {})
    assert list(client.scan_iter()) == []


def test_manifests_keep_flags_off_and_key_out_of_bff() -> None:
    for service in ("identidad", "bff-web"):
        deployment = yaml.safe_load((K8S / service / "deployment.yaml").read_text())
        environment = deployment["spec"]["template"]["spec"]["containers"][0]["env"]
        values = {item["name"]: item for item in environment}
        assert values["ENCRYPTED_LOGIN_ENABLED"]["value"] == "false"
        if service == "identidad":
            reference = values["LOGIN_ENCRYPTION_PRIVATE_KEY"]["valueFrom"]["secretKeyRef"]
            assert reference["name"] == "${LOGIN_ENCRYPTION_SECRET_NAME}"
            assert reference["key"] == "LOGIN_ENCRYPTION_PRIVATE_KEY"
        else:
            assert "LOGIN_ENCRYPTION_PRIVATE_KEY" not in values


def test_only_challenge_is_public_and_throttled() -> None:
    terraform = (ROOT / "terraform/modules/api-gateway/main.tf").read_text()
    route = terraform.split('resource "aws_apigatewayv2_route" "login_challenge" {')[1]
    assert 'route_key          = "GET /v1/sesiones/desafio"' in route.split("\n}")[0]
    assert 'authorization_type = "NONE"' in route.split("\n}")[0]
    assert "route_key              = aws_apigatewayv2_route.login_challenge.route_key" in terraform
    for name in ("v1", "movil"):
        protected = terraform.split(f'resource "aws_apigatewayv2_route" "{name}" {{')[1]
        assert 'authorization_type = "JWT"' in protected.split("\n}")[0]
    variables = (ROOT / "terraform/environments/prod/primary/variables.tf").read_text()
    paused = variables.split('variable "cicd_automatic_triggers_enabled" {')[1]
    assert "default     = false" in paused.split("\n}")[0]


@pytest.mark.parametrize("service", ["identidad", "bff-web"])
@pytest.mark.parametrize("action", ["prepare", "enable", "disable", "verify"])
def test_operation_changes_only_selected_deployment(
    monkeypatch: pytest.MonkeyPatch, service: str, action: str
) -> None:
    calls: list[tuple[str, ...]] = []
    deployment = {
        "metadata": {
            "resourceVersion": "123",
            "annotations": {"deployment.kubernetes.io/revision": "7"},
        },
        "spec": {
            "replicas": 1,
            "template": {"spec": {"containers": [{"name": service, "image": "registry/current"}]}},
        },
    }

    def run(context: str, namespace: str, *arguments: str) -> str:
        assert context == "explicit-context" and namespace == service
        calls.append(arguments)
        if arguments[:2] == ("get", "deployment"):
            return json.dumps(deployment)
        if arguments[:2] == ("get", "secret"):
            return "presente"
        return ""

    monkeypatch.setattr("scripts.configure_encrypted_login.kubectl", run)
    args = argparse.Namespace(
        context="explicit-context",
        service=service,
        action=action,
        image="registry/new" if action == "prepare" else "registry/current",
        expected_image="registry/current",
        secret_name="identidad-login-encryption",
    )
    configure(args)
    patches = [call for call in calls if call[0] == "patch"]
    if action == "verify":
        assert not patches
        return
    assert len(patches) == 1
    patch = json.loads(patches[0][-1])
    assert patch["metadata"]["resourceVersion"] == "123"
    container = patch["spec"]["template"]["spec"]["containers"][0]
    assert container["name"] == service
    assert container["env"][0]["value"] == str(action == "enable").lower()
    if action == "enable":
        checks = [call for call in calls if call[0] == "exec"]
        assert calls.index(checks[0]) < calls.index(patches[0])
        assert len(checks) == 2
        if service == "bff-web":
            assert "/oauth/login-challenge" in checks[0][-1]
            assert "127.0.0.1:8080/v1/sesiones/desafio" in checks[1][-1]


@pytest.mark.parametrize("failure", ["get", "exec", "patch", "rollout"])
def test_operation_propagates_failures(monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    def run(*arguments: Any) -> str:
        operation = arguments[2]
        if operation == failure:
            raise subprocess.CalledProcessError(1, "kubectl")
        if operation == "get":
            return json.dumps(
                {
                    "metadata": {
                        "resourceVersion": "1",
                        "annotations": {"deployment.kubernetes.io/revision": "1"},
                    },
                    "spec": {
                        "replicas": 1,
                        "template": {
                            "spec": {
                                "containers": [{"name": "identidad", "image": "registry/test"}]
                            }
                        },
                    },
                }
            )
        return ""

    monkeypatch.setattr("scripts.configure_encrypted_login.kubectl", run)
    args = argparse.Namespace(
        action="enable", service="identidad", context="test", image="registry/test"
    )
    with pytest.raises(subprocess.CalledProcessError):
        configure(args)
