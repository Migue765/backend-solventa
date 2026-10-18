import base64
import copy
import json
import subprocess
from typing import Any

import pytest
import yaml

from scripts import migrate_perfilamiento as migration


def deployment(namespace: str, name: str) -> dict[str, Any]:
    return {
        "kind": "Deployment",
        "metadata": {"namespace": namespace, "name": name},
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": name,
                            "env": [{"name": "PERFILAMIENTO_SERVICE_URL", "value": migration.URL}],
                        }
                    ]
                }
            }
        },
    }


def test_manifest_service_and_cotizacion_share_production_destination() -> None:
    directory = migration.ROOT / "deploy" / "k8s"
    perfil = yaml.safe_load((directory / "perfilamiento" / "deployment.yaml").read_text())
    service = yaml.safe_load((directory / "perfilamiento" / "service.yaml").read_text())
    cotizacion = yaml.safe_load((directory / "deployment.yaml").read_text())
    assert perfil["metadata"]["namespace"] == service["metadata"]["namespace"] == "perfilamiento"
    assert service["spec"]["selector"] == perfil["spec"]["template"]["metadata"]["labels"]
    assert perfil["spec"]["strategy"]["rollingUpdate"]["maxUnavailable"] == 0
    assert perfil["spec"]["template"]["spec"]["automountServiceAccountToken"] is False
    env = cotizacion["spec"]["template"]["spec"]["containers"][0]["env"]
    assert {"name": "PERFILAMIENTO_SERVICE_URL", "value": migration.URL} in env
    secret_refs = [
        item["valueFrom"]["secretKeyRef"]
        for item in perfil["spec"]["template"]["spec"]["containers"][0]["env"]
        if "valueFrom" in item
    ]
    assert {item["name"] for item in secret_refs} == {"perfilamiento-runtime"}
    assert {item["key"] for item in secret_refs} == set(migration.KEYS)


def test_kubectl_does_not_expose_secret_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert args[1:3] == ["--context", migration.CONTEXT]
        assert kwargs["input"] == "sensitive"
        return subprocess.CompletedProcess(args, 1, stdout="sensitive", stderr="sensitive")

    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(RuntimeError, match="kubectl fallo") as error:
        migration.kubectl("apply", "-f", "-", payload="sensitive")
    assert "sensitive" not in str(error.value)


@pytest.mark.parametrize("failure", [None, "sqlite", "irsa", "volume", "env", "secret", "image"])
def test_stage_preserves_only_required_secret_keys_and_digest(
    monkeypatch: pytest.MonkeyPatch, failure: str | None
) -> None:
    source = deployment(migration.LEGACY, migration.TARGET)
    pod = source["spec"]["template"]["spec"]
    env = [
        {"name": key, "valueFrom": {"secretKeyRef": {"name": "hu11-runtime", "key": key}}}
        for key in migration.KEYS
    ]
    env += [
        {"name": "SERVICE_APP", "value": "perfilamiento.app:create_app()"},
        {
            "name": "IDENTIDAD_SERVICE_URL",
            "value": "http://identidad.identidad.svc.cluster.local:8080",
        },
    ]
    pod["containers"][0]["env"] = env
    data = {
        "INTERNAL_API_TOKEN": base64.b64encode(b"test-token").decode(),
        "PERFILAMIENTO_DATABASE_URL": base64.b64encode(
            b"sqlite:///local.db" if failure == "sqlite" else b"postgresql://existing"
        ).decode(),
        "UNRELATED": "must-not-copy",
    }
    account: dict[str, Any] = {"metadata": {"annotations": {}}}
    if failure == "irsa":
        account["metadata"]["annotations"]["eks.amazonaws.com/role-arn"] = "role"
    if failure == "volume":
        pod["volumes"] = [{"name": "data"}]
    if failure == "env":
        env.append({"name": "UNEXPECTED", "value": "changed"})
    image = "registry/image@sha256:" + "a" * 64
    responses = [
        source,
        account,
        {
            "items": [
                {
                    "status": {
                        "containerStatuses": [
                            {
                                "name": "perfilamiento",
                                "ready": failure != "image",
                                "imageID": image,
                            }
                        ]
                    }
                }
            ]
        },
        {"data": data},
    ]
    writes: list[tuple[tuple[str, ...], str | None]] = []

    def resource(*args: str) -> dict[str, Any]:
        return responses.pop(0)

    def kubectl(*args: str, payload: str | None = None) -> str:
        if "get" in args:
            return json.dumps({"data": {}}) if failure == "secret" else ""
        writes.append((args, payload))
        return ""

    monkeypatch.setattr(migration, "resource", resource)
    monkeypatch.setattr(migration, "kubectl", kubectl)
    monkeypatch.setattr(migration, "ready", lambda: None)
    if failure:
        with pytest.raises(RuntimeError):
            migration.stage()
        assert not writes
        return
    migration.stage()
    secret = json.loads(writes[1][1] or "")
    assert secret["data"] == {key: data[key] for key in migration.KEYS}
    assert secret["metadata"]["namespace"] == migration.TARGET
    manifest = writes[2][1]
    assert manifest and image in manifest and "${IMAGE}" not in manifest
    assert all(migration.LEGACY not in args for args, _ in writes)


@pytest.mark.parametrize("failure", [None, "acceptance", "url", "reference", "controller", "hpa"])
def test_retire_is_gated_and_deletes_only_named_deployments(
    monkeypatch: pytest.MonkeyPatch, failure: str | None
) -> None:
    quote = deployment("cotizacion", "cotizacion")
    controllers = [quote, deployment("bff-web", "bff-web")]
    if failure == "url":
        quote["spec"]["template"]["spec"]["containers"][0]["env"][0]["value"] = "http://old"
    if failure == "reference":
        extra = copy.deepcopy(quote)
        extra["metadata"] = {"namespace": "preview", "name": "consumer"}
        extra["spec"]["dependency"] = migration.LEGACY
        controllers.append(extra)
    if failure == "controller":
        controllers.append(
            {"metadata": {"namespace": migration.LEGACY, "name": "unexpected"}, "kind": "Job"}
        )
    calls: list[tuple[str, ...]] = []

    def resource(*args: str) -> dict[str, Any]:
        if args == ("get", "deployments", "-A"):
            return {"items": [quote]}
        if args[-1] == "-A":
            return {"items": controllers}
        if args[-1] == "hpa":
            return {"items": [{}] if failure == "hpa" else []}
        return {"items": []}

    def kubectl(*args: str, payload: str | None = None) -> str:
        calls.append(args)
        return ""

    monkeypatch.setattr(migration, "resource", resource)
    monkeypatch.setattr(migration, "kubectl", kubectl)
    monkeypatch.setattr(migration, "ready", lambda: None)
    if failure:
        with pytest.raises(RuntimeError):
            migration.retire(acceptance_confirmed=failure != "acceptance")
        assert all("delete" not in args for args in calls)
        return
    migration.retire(acceptance_confirmed=True)
    deletes = [args for args in calls if "delete" in args]
    assert deletes == [
        (
            "-n",
            migration.LEGACY,
            "delete",
            "deployment",
            *migration.WORKLOADS,
            "--ignore-not-found",
            "--wait=true",
        )
    ]


def test_ready_requires_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(migration, "kubectl", lambda *args: "")
    monkeypatch.setattr(migration, "resource", lambda *args: {"subsets": []})
    with pytest.raises(RuntimeError, match="endpoints"):
        migration.ready()


@pytest.mark.parametrize(
    ("namespace", "service", "message"),
    [
        ("hu11-perfilamiento", "cotizacion", "HU11 esta retirado"),
        ("default", "perfilamiento", "solo se despliega"),
        ("calcular-cotizacion-preview", "cotizacion", "Preview aislado"),
        ("calcular-cotizacion-preview", "bff-web", "Preview aislado"),
        ("calcular-cotizacion-preview", "identidad", "Preview aislado"),
        ("calcular-cotizacion-preview", "perfilamiento", "Preview aislado"),
    ],
)
def test_cd_rejects_legacy_namespace_before_aws_calls(
    namespace: str, service: str, message: str
) -> None:
    import os
    import shutil

    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash no disponible")
    result = subprocess.run(
        [
            bash,
            "-c",
            "aws() { echo UNEXPECTED_AWS >&2; return 97; }; "
            'kubectl() { echo UNEXPECTED_KUBECTL >&2; return 98; }; source "$1"',
            "test-cd-guard",
            "deploy/codebuild/deploy.sh",
        ],
        cwd=migration.ROOT,
        env={
            **os.environ,
            "CLUSTER_NAME": "test",
            "NAMESPACE": namespace,
            "SERVICE_NAME": service,
            "ECR_REPOSITORY_URL": "test",
            "IMAGE_TAG": "test",
            "AWS_REGION": "us-east-2",
        },
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 1
    assert message in result.stderr
    assert "UNEXPECTED_AWS" not in result.stdout + result.stderr
    assert "UNEXPECTED_KUBECTL" not in result.stdout + result.stderr
