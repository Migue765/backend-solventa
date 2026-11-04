import json
import os
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
K8S = ROOT / "deploy" / "k8s"
SERVICES = (
    "bff-movil",
    "bff-web",
    "cotizacion",
    "identidad",
    "perfilamiento",
    "polizas",
    "siniestros",
)
HPA_SERVICES = {"bff-movil", "bff-web", "cotizacion", "identidad"}
BFF_PATCH_PREFIX = "kubectl|-n bff-web patch deployment/bff-web --type=strategic -p "


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


def local_kubectl() -> str:
    executable = shutil.which("kubectl")
    if executable is None:
        pytest.skip("kubectl no disponible para verificar el renderizado y merge locales")
    return executable


def run_deploy(
    service: str, failure: str = "", *, root: Path = ROOT, previous: str = "registry/previous:1"
) -> subprocess.CompletedProcess[str]:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash no disponible")
    kubectl = local_kubectl() if service == "bff-web" else ""
    return subprocess.run(
        [
            bash,
            "-c",
            """
aws() { printf 'aws|%s\\n' "$*" >&2; }
envsubst() { cat; }
rolled_back=false
kubectl() {
  printf 'kubectl|%s\\n' "$*" >&2
  if [ "$*" = "$FAIL_COMMAND" ]; then return 23; fi
  case "$*" in
    "set image --local "*)
      if [ "$FAIL_COMMAND" = "render" ]; then return 23; fi
      command "$KUBECTL_LOCAL" "$@"
      return $? ;;
    *" patch deployment/bff-web "*)
      if [ "$FAIL_COMMAND" = "patch" ]; then return 23; fi ;;
    *" rollout status "*)
      case "$FAIL_COMMAND" in
        rollout|rollback|rollback-wait)
          if [ "$rolled_back" = "false" ] || [ "$FAIL_COMMAND" = "rollback-wait" ]; then
            return 23
          fi ;;
      esac ;;
    *" rollout undo "*)
      if [ "$FAIL_COMMAND" = "rollback" ]; then return 23; fi
      rolled_back=true ;;
    *" get deployment "*) printf '%s\\n' "$PREVIOUS_IMAGE" ;;
    "apply -f -") cat >/dev/null ;;
  esac
  return 0
}
source "$1"
""",
            "test-academic-deploy",
            "deploy/codebuild/deploy.sh",
        ],
        cwd=root,
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
            "KUBECTL_LOCAL": kubectl,
            "PREVIOUS_IMAGE": previous,
        },
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )


def bff_patch(result: subprocess.CompletedProcess[str]) -> dict:
    patches = [
        call.removeprefix(BFF_PATCH_PREFIX)
        for call in result.stderr.splitlines()
        if call.startswith(BFF_PATCH_PREFIX)
    ]
    assert len(patches) == 1, result.stderr
    patch = json.loads(patches[0])
    assert isinstance(patch, dict)
    return patch


@pytest.mark.parametrize("previous_url", ["missing", "old", "secret"])
def test_bff_deploy_updates_image_and_profile_dependency_in_one_patch(previous_url: str) -> None:
    result = run_deploy("bff-web")
    assert result.returncode == 0, result.stdout + result.stderr
    patch = bff_patch(result)
    manifest = yaml.safe_load(
        (service_directory("bff-web") / "deployment.yaml").read_text(encoding="utf-8")
    )
    declared_env = manifest["spec"]["template"]["spec"]["containers"][0]["env"]
    dependency = next(item for item in declared_env if item["name"] == "PERFILAMIENTO_SERVICE_URL")
    claim_dependency = next(
        item for item in declared_env if item["name"] == "SINIESTROS_SERVICE_URL"
    )
    assert patch == {
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": "bff-web",
                            "image": "registry/test:test",
                            "env": [
                                {**dependency, "valueFrom": None},
                                {**claim_dependency, "valueFrom": None},
                            ],
                        }
                    ]
                }
            }
        }
    }
    assert "set image deployment/bff-web" not in result.stderr
    assert "set env" not in result.stderr
    assert "kubectl|apply -f -" not in result.stderr
    assert "kubectl|apply -f deploy/k8s/bff-web/deployment.yaml" not in result.stderr
    runtime = deepcopy(manifest)
    pod = runtime["spec"]["template"]["spec"]
    container = pod["containers"][0]
    container["image"] = "registry/previous:1"
    container["env"] = [item for item in container["env"] if item["name"] != dependency["name"]]
    for item in container["env"]:
        if item["name"] == "ENCRYPTED_LOGIN_ENABLED":
            item["value"] = "true"
    container["env"].extend(
        [
            {"name": "RUNTIME_ONLY", "value": "preservar"},
            {
                "name": "RUNTIME_SECRET",
                "valueFrom": {"secretKeyRef": {"name": "runtime-secret", "key": "token"}},
            },
        ]
    )
    container["envFrom"] = [{"secretRef": {"name": "runtime-env"}}]
    pod["containers"].insert(0, {"name": "sidecar", "image": "sidecar:previous"})
    if previous_url != "missing":
        previous_dependency: dict[str, object] = (
            {"value": "http://localhost:5003"}
            if previous_url == "old"
            else {"valueFrom": {"configMapKeyRef": {"name": "legacy-url", "key": "url"}}}
        )
        container["env"].append({"name": dependency["name"], **previous_dependency})
    expected = deepcopy(runtime)
    expected_container = expected["spec"]["template"]["spec"]["containers"][1]
    expected_container["image"] = "registry/test:test"
    # El merge local conserva null al insertar una variable, no al actualizarla.
    expected_dependency = (
        {**dependency, "valueFrom": None} if previous_url == "missing" else dependency
    )
    expected_container["env"] = [
        item for item in expected_container["env"] if item["name"] != dependency["name"]
    ] + [expected_dependency]
    applied = subprocess.run(
        [
            local_kubectl(),
            "patch",
            "--local",
            "-f",
            "-",
            "--type=strategic",
            "-p",
            json.dumps(patch),
            "-o",
            "json",
        ],
        input=json.dumps(runtime),
        text=True,
        capture_output=True,
        timeout=10,
        check=True,
    )
    actual = json.loads(applied.stdout)
    for document in (actual, expected):
        for entry in document["spec"]["template"]["spec"]["containers"]:
            if "env" in entry:
                entry["env"].sort(key=lambda variable: variable["name"])
    assert actual["spec"] == expected["spec"]


def bff_workspace(tmp_path: Path) -> Path:
    for relative in (
        "deploy/codebuild/deploy.sh",
        "deploy/k8s/bff-web/deployment.yaml",
        "deploy/k8s/bff-web/hpa.yaml",
    ):
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)
    return tmp_path / "deploy" / "k8s" / "bff-web" / "deployment.yaml"


def test_bff_dependency_comes_from_manifest_not_a_hardcoded_url(tmp_path: Path) -> None:
    path = bff_workspace(tmp_path)
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    environment = manifest["spec"]["template"]["spec"]["containers"][0]["env"]
    dependency = next(item for item in environment if item["name"] == "PERFILAMIENTO_SERVICE_URL")
    dependency["value"] = "http://perfilamiento.example.test:8081"
    path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    result = run_deploy("bff-web", root=tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert bff_patch(result)["spec"]["template"]["spec"]["containers"][0]["env"] == [
        {**dependency, "valueFrom": None},
        {
            **next(item for item in environment if item["name"] == "SINIESTROS_SERVICE_URL"),
            "valueFrom": None,
        },
    ]


@pytest.mark.parametrize("missing", [True, False])
@pytest.mark.parametrize("name", ["PERFILAMIENTO_SERVICE_URL", "SINIESTROS_SERVICE_URL"])
def test_bff_dependency_missing_or_empty_stops_before_mutations(
    tmp_path: Path, missing: bool, name: str
) -> None:
    path = bff_workspace(tmp_path)
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    environment = manifest["spec"]["template"]["spec"]["containers"][0]["env"]
    dependency = next(item for item in environment if item["name"] == name)
    if missing:
        environment.remove(dependency)
    else:
        dependency["value"] = ""
    path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    result = run_deploy("bff-web", root=tmp_path)
    assert result.returncode != 0
    assert "Falta PERFILAMIENTO_SERVICE_URL o SINIESTROS_SERVICE_URL" in result.stderr
    assert not any(command in result.stderr for command in ("apply -f", " scale ", " patch "))
    assert "completado." not in result.stdout


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        ("render", "No fue posible preparar la actualizacion"),
        ("patch", "No se confirmo la actualizacion"),
    ],
)
def test_bff_update_failure_is_explicit_and_does_not_roll_back_an_unknown_revision(
    failure: str, message: str
) -> None:
    result = run_deploy("bff-web", failure)
    assert result.returncode != 0
    assert message in result.stderr
    assert " rollout " not in result.stderr
    assert "completado." not in result.stdout


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        ("rollout", "Rollback de bff-web confirmado."),
        ("rollback", "No fue posible iniciar el rollback de bff-web."),
        ("rollback-wait", "El rollback de bff-web no quedo confirmado."),
    ],
)
def test_bff_rollout_failure_reverts_the_single_template_change(failure: str, message: str) -> None:
    result = run_deploy("bff-web", failure)
    assert result.returncode != 0
    bff_patch(result)
    assert result.stderr.count("kubectl|-n bff-web rollout undo deployment/bff-web") == 1
    assert message in result.stderr
    assert "set image deployment/bff-web" not in result.stderr
    assert "completado." not in result.stdout


@pytest.mark.parametrize("service", SERVICES)
def test_deploy_limits_replicas_before_rollout_and_checks_result(service: str) -> None:
    result = run_deploy(service)
    assert result.returncode == 0, result.stdout + result.stderr
    calls = result.stderr.splitlines()
    rollout = calls.index(
        f"kubectl|-n {service} rollout status deployment/{service} --timeout=300s"
    )
    if service in {"polizas", "siniestros"}:
        assert calls.index("kubectl|apply -f -") < rollout
        assert not any(" scale " in call for call in calls)
    else:
        scale = calls.index(f"kubectl|-n {service} scale deployment/{service} --replicas=1")
        if service == "bff-web":
            image = next(
                index for index, call in enumerate(calls) if call.startswith(BFF_PATCH_PREFIX)
            )
        else:
            image = calls.index(
                f"kubectl|-n {service} set image deployment/{service} {service}=registry/test:test"
            )
        assert scale < image < rollout
        if service in HPA_SERVICES:
            path = (service_directory(service) / "hpa.yaml").relative_to(ROOT).as_posix()
            assert calls.index(f"kubectl|apply -f {path}") < scale
    if service not in HPA_SERVICES:
        assert not any("hpa.yaml" in call for call in calls)
    if service != "bff-web":
        assert not any(" patch " in call or "--local" in call for call in calls)
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


def test_first_polizas_rollout_failure_has_no_previous_revision_to_undo() -> None:
    result = run_deploy("polizas", "rollout", previous="")
    assert result.returncode != 0
    assert " rollout undo " not in result.stderr
    assert "completado." not in result.stdout


def test_claims_are_routed_through_authenticated_bff_and_internal_service() -> None:
    gateway = (ROOT / "terraform" / "modules" / "api-gateway" / "main.tf").read_text(
        encoding="utf-8"
    )
    routing = gateway.split('resource "aws_lb_listener_rule" "bff_web_claims" {', 1)[1]
    routing = routing.split('resource "', 1)[0]
    assert 'values = ["/v1/siniestros", "/v1/siniestros/*"]' in routing
    assert "target_group_arn = aws_lb_target_group.bff_web.arn" in routing
    assert 'route_key          = "ANY /v1/{proxy+}"' in gateway
    assert 'authorization_type = "JWT"' in gateway
    service = yaml.safe_load((K8S / "siniestros" / "service.yaml").read_text(encoding="utf-8"))
    assert service["spec"]["type"] == "ClusterIP"
    deployment = yaml.safe_load(
        (K8S / "siniestros" / "deployment.yaml").read_text(encoding="utf-8")
    )
    environment = deployment["spec"]["template"]["spec"]["containers"][0]["env"]
    database = next(item for item in environment if item["name"] == "SINIESTROS_DATABASE_URL")
    assert database["valueFrom"]["secretKeyRef"] == {
        "name": "siniestros-runtime",
        "key": "SINIESTROS_DATABASE_URL",
    }
    pod = deployment["spec"]["template"]["spec"]
    container = pod["containers"][0]
    storage = next(item for item in environment if item["name"] == "SINIESTROS_STORAGE_ROOT")
    assert storage["value"] == "/var/lib/solventa/evidencias"
    assert container["volumeMounts"] == [{"name": "evidencias", "mountPath": "/var/lib/solventa"}]
    assert pod["volumes"] == [
        {"name": "evidencias", "persistentVolumeClaim": {"claimName": "siniestros-evidencias"}}
    ]
    assert pod["securityContext"]["fsGroup"] == 10001
