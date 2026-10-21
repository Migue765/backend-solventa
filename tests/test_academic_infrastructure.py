import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
K8S = ROOT / "deploy" / "k8s"
SERVICES = ("bff-movil", "bff-web", "cotizacion", "identidad", "perfilamiento", "polizas")
HPA_SERVICES = {"bff-movil", "bff-web", "cotizacion", "identidad"}


def service_directory(service: str) -> Path:
    return K8S if service == "cotizacion" else K8S / service


@pytest.mark.parametrize("path", sorted(K8S.rglob("*.yaml")), ids=lambda p: str(p.relative_to(K8S)))
def test_kubernetes_manifests_parse(path: Path) -> None:
    for manifest in yaml.safe_load_all(path.read_text(encoding="utf-8")):
        assert isinstance(manifest, dict)
        assert manifest["apiVersion"]
        assert manifest["kind"]
        assert manifest["metadata"]["name"]


def test_all_api_deployments_keep_one_replica_and_rollout_headroom() -> None:
    deployments = [
        yaml.safe_load(path.read_text(encoding="utf-8")) for path in K8S.rglob("deployment.yaml")
    ]
    assert {item["metadata"]["name"] for item in deployments} == set(SERVICES)
    for deployment in deployments:
        assert deployment["spec"]["replicas"] == 1
        assert deployment["spec"]["strategy"]["type"] == "RollingUpdate"
        assert deployment["spec"]["strategy"]["rollingUpdate"] == {
            "maxSurge": 1,
            "maxUnavailable": 0,
        }


def test_existing_autoscalers_and_disruption_budgets_preserve_academic_profile() -> None:
    autoscalers = [
        yaml.safe_load(path.read_text(encoding="utf-8")) for path in K8S.rglob("hpa.yaml")
    ]
    assert {item["spec"]["scaleTargetRef"]["name"] for item in autoscalers} == HPA_SERVICES
    for autoscaler in autoscalers:
        assert autoscaler["spec"]["minReplicas"] == autoscaler["spec"]["maxReplicas"] == 1
        assert autoscaler["spec"]["scaleTargetRef"]["kind"] == "Deployment"
        target = autoscaler["spec"]["scaleTargetRef"]["name"]
        deployment = yaml.safe_load(
            (service_directory(target) / "deployment.yaml").read_text(encoding="utf-8")
        )
        assert autoscaler["metadata"]["namespace"] == deployment["metadata"]["namespace"]
    budgets = [yaml.safe_load(path.read_text(encoding="utf-8")) for path in K8S.rglob("pdb.yaml")]
    assert len(budgets) == 4
    assert all(item["spec"]["minAvailable"] == 1 for item in budgets)


def run_deploy(service: str, failure: str = "") -> subprocess.CompletedProcess[str]:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash no disponible")
    return subprocess.run(
        [
            bash,
            "-c",
            """
aws() { printf 'aws|%s\\n' "$*" >&2; }
envsubst() { cat; }
kubectl() {
  printf 'kubectl|%s\\n' "$*" >&2
  if [ "$*" = "$FAIL_COMMAND" ]; then return 23; fi
  case "$*" in
    *" get deployment "*) printf 'registry/previous:1\\n' ;;
    "apply -f -") cat >/dev/null ;;
  esac
  return 0
}
source "$1"
""",
            "test-academic-deploy",
            "deploy/codebuild/deploy.sh",
        ],
        cwd=ROOT,
        env={
            **os.environ,
            "CLUSTER_NAME": "test",
            "NAMESPACE": service,
            "SERVICE_NAME": service,
            "ECR_REPOSITORY_URL": "registry/test",
            "IMAGE_TAG": "test",
            "AWS_REGION": "us-east-2",
            "MIGRATE": "false",
            "FAIL_COMMAND": failure,
        },
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )


@pytest.mark.parametrize("service", SERVICES)
def test_deploy_limits_replicas_before_rollout_and_checks_result(service: str) -> None:
    result = run_deploy(service)
    assert result.returncode == 0, result.stdout + result.stderr
    calls = result.stderr.splitlines()
    rollout = calls.index(
        f"kubectl|-n {service} rollout status deployment/{service} --timeout=300s"
    )
    if service == "polizas":
        assert calls.index("kubectl|apply -f -") < rollout
        assert not any(" scale " in call for call in calls)
    else:
        scale = calls.index(f"kubectl|-n {service} scale deployment/{service} --replicas=1")
        image = calls.index(
            f"kubectl|-n {service} set image deployment/{service} {service}=registry/test:test"
        )
        assert scale < image < rollout
        if service in HPA_SERVICES:
            path = (service_directory(service) / "hpa.yaml").relative_to(ROOT).as_posix()
            assert calls.index(f"kubectl|apply -f {path}") < scale
    if service not in HPA_SERVICES:
        assert not any("hpa.yaml" in call for call in calls)
    assert "completado." in result.stdout


@pytest.mark.parametrize(
    ("service", "failure"),
    [
        ("cotizacion", "apply -f deploy/k8s/hpa.yaml"),
        ("cotizacion", "-n cotizacion scale deployment/cotizacion --replicas=1"),
        ("polizas", "apply -f -"),
        ("cotizacion", "-n cotizacion rollout status deployment/cotizacion --timeout=300s"),
    ],
)
def test_deploy_does_not_report_success_when_capacity_or_rollout_fails(
    service: str, failure: str
) -> None:
    result = run_deploy(service, failure)
    assert result.returncode != 0
    assert "completado." not in result.stdout
    if "rollout status" in failure:
        assert "rollout undo deployment/cotizacion" in result.stderr
    else:
        assert " set image " not in result.stderr
        assert " rollout status " not in result.stderr
