import json
import os
import re
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
K8S = ROOT / "deploy" / "k8s"
PRIMARY = ROOT / "terraform" / "environments" / "prod" / "primary"
SERVICES = (
    "asistencias",
    "bff-movil",
    "bff-web",
    "cotizacion",
    "identidad",
    "notificaciones",
    "perfilamiento",
    "polizas",
    "siniestros",
)
NEW_SERVICES = ("siniestros", "notificaciones", "asistencias")
FULL_MANIFEST_SERVICES = {"asistencias", "notificaciones", "polizas", "siniestros"}
HPA_SERVICES = {
    "bff-movil",
    "bff-web",
    "cotizacion",
    "identidad",
    "notificaciones",
    "perfilamiento",
    "polizas",
}
BFF_PATCH_PREFIX = "kubectl|-n bff-web patch deployment/bff-web --type=strategic -p "
IRSA_ROLE = "arn:aws:iam::123456789012:role/solventa-prod-siniestros"


def service_directory(service: str) -> Path:
    return K8S if service == "cotizacion" else K8S / service


def test_primary_forwards_external_boundary_to_every_new_role() -> None:
    config = "\n".join(path.read_text() for path in PRIMARY.glob("*.tf"))
    assert not re.search(r'^resource "aws_iam_role" ', config, re.M)
    roles = {"network": 0, "ci": 0, "application": 0}
    for name, body in re.findall(r'^module "([^"]+)" \{\n(.*?)^\}', config, re.M | re.S):
        source = re.search(r'^\s*source\s*=\s*"([^"]+)"', body, re.M)
        assert source is not None, name
        directory = (PRIMARY / source[1]).resolve()
        module = "\n".join(path.read_text() for path in directory.glob("*.tf"))
        declared = re.findall(
            r'^resource "aws_iam_role" "([^"]+)" \{\n(.*?)^\}', module, re.M | re.S
        )
        if not declared:
            continue
        for role_name, role in declared:
            family = (
                "ci"
                if directory.name == "cicd"
                else "network"
                if directory.name == "eks" or role_name == "alb_controller"
                else "application"
            )
            parameter = (
                "alb_permissions_boundary_arn"
                if role_name == "alb_controller"
                else "permissions_boundary_arn"
            )
            assert re.search(
                rf"^\s*{parameter}\s*=\s*var\.permissions_boundary_arns\.{family}\s*$",
                body,
                re.M,
            ), name
            assert re.search(rf"^\s*permissions_boundary\s*=\s*var\.{parameter}\s*$", role, re.M), (
                name
            )
            roles[family] += 1
    assert roles == {"network": 3, "ci": 27, "application": 3}, (
        "Revisar el inventario completo si cambian los roles de esta reconstruccion."
    )


def test_primary_oidc_exception_keeps_provider_guards_and_other_eks_tags() -> None:
    versions = (PRIMARY / "versions.tf").read_text()
    providers = re.findall(r'^provider "aws" \{\n(.*?)^\}', versions, re.M | re.S)
    assert len(providers) == 2
    for provider in providers:
        for field, value in {
            "region": "var.aws_region",
            "profile": "var.aws_profile",
            "allowed_account_ids": "[var.account_id]",
        }.items():
            assert re.search(rf"^\s*{field}\s*=\s*{re.escape(value)}$", provider, re.M)
        if re.search(r'^\s*alias\s*=\s*"eks_explicit_tags"$', provider, re.M):
            assert "default_tags" not in provider
        else:
            assert re.search(r"default_tags \{\s+tags\s*=\s*local.tags", provider)
    assert 'startswith(self.arn, "arn:aws:sts::${var.account_id}:assumed-role/")' in versions
    config = "\n".join(path.read_text() for path in PRIMARY.glob("*.tf"))
    assert config.count("aws.eks_explicit_tags") == 1
    eks = re.search(r'^module "eks" \{\n(.*?)^\}', config, re.M | re.S)
    assert eks is not None
    assert re.search(r"aws\s*=\s*aws.eks_explicit_tags", eks[1])
    assert re.search(r"oidc_provider_tags\s*=\s*\{\}", eks[1])
    assert re.search(r"^\s*tags\s*=\s*local.tags$", eks[1], re.M)
    module = (ROOT / "terraform" / "modules" / "eks" / "main.tf").read_text()
    tagged = {
        f"{kind}.{name}": body
        for kind, name, body in re.findall(
            r'^resource "([^"]+)" "([^"]+)" \{\n(.*?)^\}', module, re.M | re.S
        )
        if re.search(r"^\s*tags\s*=", body, re.M)
    }
    assert set(tagged) == {
        "aws_iam_role.cluster",
        "aws_iam_role.node",
        "aws_eks_cluster.this",
        "aws_eks_access_entry.admin",
        "aws_launch_template.node",
        "aws_eks_node_group.default",
        "aws_eks_node_group.additional",
        "aws_eks_addon.this",
        "aws_iam_openid_connect_provider.this",
    }, "Incluir cualquier recurso nuevo etiquetable en las assertions tags/tags_all offline."
    for resource, body in tagged.items():
        if resource not in {"aws_eks_cluster.this", "aws_iam_openid_connect_provider.this"}:
            assert re.search(r"^\s*tags\s*=\s*var.tags$", body, re.M), resource


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
    assert len(budgets) == len(HPA_SERVICES)
    assert {item["metadata"]["namespace"] for item in budgets} == HPA_SERVICES
    assert all(item["spec"]["minAvailable"] == 1 for item in budgets)


def local_kubectl() -> str:
    executable = shutil.which("kubectl")
    if executable is None:
        pytest.skip("kubectl no disponible para verificar el renderizado y merge locales")
    return executable


def run_deploy(
    service: str,
    failure: str = "",
    *,
    root: Path = ROOT,
    previous: str = "registry/previous:1",
    namespace: str | None = None,
    migrate: bool = False,
    service_account_role: str = IRSA_ROLE,
    evidence_bucket: str = "solventa-test-siniestros-evidencias-123456789012",
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
    *" get serviceaccount "*) printf '%s\\n' "$SERVICE_ACCOUNT_ROLE" ;;
    "apply -f -") sed -n 's/^kind: /applied|/p' >&2 ;;
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
            "NAMESPACE": namespace or service,
            "SERVICE_NAME": service,
            "ECR_REPOSITORY_URL": "registry/test",
            "IMAGE_TAG": "test",
            "AWS_REGION": "us-east-2",
            "MIGRATE": "true" if migrate else "false",
            "FAIL_COMMAND": failure,
            "KUBECTL_LOCAL": kubectl,
            "PREVIOUS_IMAGE": previous,
            "SERVICE_ACCOUNT_ROLE": service_account_role,
            "SINIESTROS_EVIDENCIAS_BUCKET": evidence_bucket,
        },
        text=True,
        capture_output=True,
        timeout=60,
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
    container["env"] = [
        item
        for item in container["env"]
        if item["name"] not in {dependency["name"], "SINIESTROS_SERVICE_URL"}
    ]
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
        container["env"].append({"name": "SINIESTROS_SERVICE_URL", **previous_dependency})
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
        timeout=60,
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
    ]


@pytest.mark.parametrize("missing", [True, False])
@pytest.mark.parametrize("name", ["PERFILAMIENTO_SERVICE_URL", "SINIESTROS_SERVICE_URL"])
def test_bff_requires_only_active_profile_dependency(
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
    if name == "SINIESTROS_SERVICE_URL":
        assert result.returncode == 0, result.stderr
        environment = bff_patch(result)["spec"]["template"]["spec"]["containers"][0]["env"]
        assert all(item["name"] != name for item in environment)
        return
    assert result.returncode != 0
    assert "Falta PERFILAMIENTO_SERVICE_URL" in result.stderr
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


@pytest.mark.parametrize(
    "role", ["", "${IRSA_ROLE_ARN}", "arn:aws:iam::123456789012:user/operador"]
)
def test_claims_deploy_requires_irsa_service_account_before_any_change(role: str) -> None:
    result = run_deploy("siniestros", migrate=True, service_account_role=role)
    assert result.returncode != 0
    assert "Falta el ServiceAccount siniestros" in result.stderr
    assert "aws|eks update-kubeconfig" in result.stderr
    assert not any(
        command in result.stderr for command in ("apply -f", " wait ", " rollout ", " scale ")
    )
    assert "completado." not in result.stdout


@pytest.mark.parametrize("service", NEW_SERVICES)
def test_new_services_only_deploy_to_their_production_namespace(service: str) -> None:
    result = run_deploy(service, namespace="default")
    assert result.returncode != 0
    assert f"{service} solo se despliega en el namespace productivo {service}." in result.stderr
    assert "aws|" not in result.stderr
    assert "kubectl|" not in result.stderr


@pytest.mark.parametrize("service", NEW_SERVICES)
def test_new_services_migrate_before_applying_their_manifests(service: str) -> None:
    result = run_deploy(service, migrate=True)
    assert result.returncode == 0, result.stdout + result.stderr
    calls = result.stderr.splitlines()
    wait = calls.index(
        f"kubectl|-n {service} wait --for=condition=complete job/{service}-migrate-test"
        " --timeout=300s"
    )
    assert calls.index("applied|Job") < wait < calls.index("applied|Deployment")


@pytest.mark.parametrize("service", SERVICES)
def test_deploy_limits_replicas_before_rollout_and_checks_result(service: str) -> None:
    result = run_deploy(service)
    assert result.returncode == 0, result.stdout + result.stderr
    calls = result.stderr.splitlines()
    rollout = calls.index(
        f"kubectl|-n {service} rollout status deployment/{service} --timeout=300s"
    )
    if service in FULL_MANIFEST_SERVICES:
        service_manifest = f"kubectl|apply -f deploy/k8s/{service}/service.yaml"
        assert calls.index(service_manifest) < calls.index("applied|Deployment") < rollout
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
        hpa = calls.index(f"kubectl|apply -f {path}")
        if service in FULL_MANIFEST_SERVICES:
            assert hpa < calls.index("applied|Deployment")
        else:
            assert hpa < scale
    else:
        assert not any("hpa.yaml" in call for call in calls)
    if service != "bff-web":
        assert not any(" patch " in call or "--local" in call for call in calls)
    if service not in {"identidad", "notificaciones", "siniestros"}:
        assert "applied|CronJob" not in calls
    assert "completado." in result.stdout


@pytest.mark.parametrize("service", ["notificaciones", "siniestros"])
def test_notification_scheduler_starts_only_after_successful_rollout(service: str) -> None:
    result = run_deploy(service)
    assert result.returncode == 0, result.stdout + result.stderr
    calls = result.stderr.splitlines()
    rollout = calls.index(
        f"kubectl|-n {service} rollout status deployment/{service} --timeout=300s"
    )
    assert calls.index("applied|Deployment") < rollout < calls.index("applied|CronJob")
    failed = run_deploy(service, "rollout")
    assert failed.returncode != 0
    assert f"Rollback de {service} confirmado." in failed.stderr
    assert "applied|CronJob" not in failed.stderr
    assert "completado." not in failed.stdout


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
    assert 'values = ["/v1/siniestros", "/v1/siniestros/*", "/v1/administracion/*"]' in routing
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
    assert pod["volumes"] == [{"name": "evidencias", "emptyDir": {"sizeLimit": "2Gi"}}]
    assert pod["securityContext"]["fsGroup"] == 10001


def test_claims_parametric_workers_are_redundant_and_protected_by_budgets() -> None:
    workers = list(
        yaml.safe_load_all((K8S / "siniestros" / "workers.yaml").read_text(encoding="utf-8"))
    )
    budgets = list(
        yaml.safe_load_all((K8S / "siniestros" / "workers-pdb.yaml").read_text(encoding="utf-8"))
    )
    api = yaml.safe_load((K8S / "siniestros" / "deployment.yaml").read_text(encoding="utf-8"))
    api_environment = {
        item["name"]: item for item in api["spec"]["template"]["spec"]["containers"][0]["env"]
    }
    commands: dict[str, list[str]] = {}
    replicas: dict[str, int] = {}
    for worker in workers:
        assert worker["kind"] == "Deployment"
        assert worker["metadata"]["namespace"] == "siniestros"
        name = worker["metadata"]["name"]
        replicas[name] = worker["spec"]["replicas"]
        assert replicas[name] >= 2
        assert worker["spec"]["strategy"]["rollingUpdate"] == {"maxSurge": 1, "maxUnavailable": 0}
        labels = worker["spec"]["template"]["metadata"]["labels"]
        assert worker["spec"]["selector"]["matchLabels"] == {"app.kubernetes.io/name": name}
        assert labels["app.kubernetes.io/name"] == name != "siniestros"
        pod = worker["spec"]["template"]["spec"]
        assert pod["automountServiceAccountToken"] is False
        assert "serviceAccountName" not in pod
        assert pod["securityContext"]["runAsNonRoot"] is True
        container = pod["containers"][0]
        assert container["image"] == "${IMAGE}"
        assert container["securityContext"]["capabilities"]["drop"] == ["ALL"]
        assert container["securityContext"]["allowPrivilegeEscalation"] is False
        commands[name] = container["command"]
        environment = {item["name"]: item for item in container["env"]}
        for key in ("INTERNAL_API_TOKEN", "SINIESTROS_DATABASE_URL", "POLIZAS_SERVICE_URL"):
            assert environment[key] == api_environment[key]
        assert environment["SINIESTROS_STORAGE_ROOT"]["value"] == "/var/lib/solventa/evidencias"
        assert "FUENTES_PARAMETRICAS_JSON" not in environment
        assert "SINIESTROS_EVIDENCIAS_BUCKET" not in environment
    assert commands == {
        "siniestros-eventos-parametricos": [
            "python",
            "-m",
            "siniestros.parametric_worker",
            "--limit",
            "5000",
        ],
        "siniestros-pagos-parametricos": [
            "python",
            "-m",
            "siniestros.payment_worker",
            "--limit",
            "100",
        ],
    }
    protected = {}
    for budget in budgets:
        assert budget["kind"] == "PodDisruptionBudget"
        assert budget["metadata"]["namespace"] == "siniestros"
        protected[budget["spec"]["selector"]["matchLabels"]["app.kubernetes.io/name"]] = budget[
            "spec"
        ]["minAvailable"]
    assert set(protected) == set(replicas)
    assert all(1 <= protected[name] < replicas[name] for name in replicas)


def test_claims_workers_deploy_only_after_api_rollout_and_revert_on_failure() -> None:
    result = run_deploy("siniestros")
    assert result.returncode == 0, result.stdout + result.stderr
    calls = result.stderr.splitlines()
    api = calls.index("kubectl|-n siniestros rollout status deployment/siniestros --timeout=300s")
    budgets = calls.index("kubectl|apply -f deploy/k8s/siniestros/workers-pdb.yaml")
    assert api < budgets
    assert calls[budgets:].count("applied|Deployment") == 2
    for worker in ("siniestros-eventos-parametricos", "siniestros-pagos-parametricos"):
        rollout = f"kubectl|-n siniestros rollout status deployment/{worker} --timeout=300s"
        assert calls.index(rollout) > budgets

    worker_failure = run_deploy(
        "siniestros",
        "-n siniestros rollout status deployment/siniestros-pagos-parametricos --timeout=300s",
    )
    assert worker_failure.returncode != 0
    assert "rollout undo deployment/siniestros-pagos-parametricos" in worker_failure.stderr
    assert "completado." not in worker_failure.stdout

    api_failure = run_deploy("siniestros", "rollout")
    assert api_failure.returncode != 0
    assert "workers" not in api_failure.stderr


def terraform_default(name: str) -> str:
    variables = (PRIMARY / "variables.tf").read_text(encoding="utf-8")
    block = variables.split(f'variable "{name}" {{', 1)[1].split("\n}", 1)[0]
    match = re.search(r'default\s*=\s*"([^"]+)"', block)
    assert match is not None, name
    return match.group(1)


def test_claims_evidence_bucket_and_irsa_role_match_terraform() -> None:
    claims = re.sub(r"\s+", " ", (PRIMARY / "siniestros.tf").read_text(encoding="utf-8"))
    assert 'bucket_name = "${var.name_prefix}-siniestros-evidencias-${var.account_id}"' in claims
    assert (
        's3_object_arns = ["${module.s3_siniestros_evidencias.bucket_arn}/evidencias/*"]' in claims
    )
    assert 'service_namespace = "siniestros" service_account_name = "siniestros"' in claims
    storage = (ROOT / "services" / "siniestros" / "src" / "siniestros" / "storage.py").read_text(
        encoding="utf-8"
    )
    assert 'PREFIX = "evidencias/"' in storage
    deployment = yaml.safe_load(
        (K8S / "siniestros" / "deployment.yaml").read_text(encoding="utf-8")
    )
    pod = deployment["spec"]["template"]["spec"]
    environment = {item["name"]: item.get("value") for item in pod["containers"][0]["env"]}
    assert environment["SINIESTROS_EVIDENCIAS_BUCKET"] == "${SINIESTROS_EVIDENCIAS_BUCKET}"
    assert "siniestros_evidence_bucket = module.s3_siniestros_evidencias.bucket_name" in claims
    pipeline = (ROOT / "terraform/modules/cicd/main.tf").read_text(encoding="utf-8")
    assert 'name  = "SINIESTROS_EVIDENCIAS_BUCKET"' in pipeline
    assert "value = environment_variable.value" in pipeline
    assert environment["AWS_REGION"] == "${AWS_REGION}"
    account = yaml.safe_load(
        (K8S / "siniestros" / "serviceaccount.yaml").read_text(encoding="utf-8")
    )
    assert pod["serviceAccountName"] == account["metadata"]["name"] == "siniestros"
    assert account["metadata"]["namespace"] == deployment["metadata"]["namespace"]
    assert account["metadata"]["annotations"] == {"eks.amazonaws.com/role-arn": "${IRSA_ROLE_ARN}"}


def test_claims_missing_bucket_fails_before_aws_or_kubernetes() -> None:
    result = run_deploy("siniestros", evidence_bucket="")
    assert result.returncode != 0
    assert "Indicar el bucket de evidencias de esta cuenta" in result.stderr
    assert "aws|" not in result.stderr
    assert "kubectl|" not in result.stderr


def test_claims_manifest_renders_the_selected_account_bucket() -> None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash no disponible")
    bucket = "solventa-test-siniestros-evidencias-123456789012"
    result = subprocess.run(
        [
            bash,
            "-c",
            "command -v envsubst >/dev/null || exit 77; "
            "envsubst '${SINIESTROS_EVIDENCIAS_BUCKET}' < deploy/k8s/siniestros/deployment.yaml",
        ],
        cwd=ROOT,
        env={**os.environ, "SINIESTROS_EVIDENCIAS_BUCKET": bucket},
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    if result.returncode == 77:
        pytest.skip("envsubst no disponible para renderizado local")
    assert result.returncode == 0, result.stderr
    deployment = yaml.safe_load(result.stdout)
    environment = deployment["spec"]["template"]["spec"]["containers"][0]["env"]
    selected = next(item for item in environment if item["name"] == "SINIESTROS_EVIDENCIAS_BUCKET")
    assert selected["value"] == bucket


def test_internal_urls_point_to_declared_services() -> None:
    declared: dict[tuple[str, str], set[int]] = {}
    for path in K8S.rglob("service.yaml"):
        service = yaml.safe_load(path.read_text(encoding="utf-8"))
        key = (service["metadata"]["name"], service["metadata"]["namespace"])
        declared[key] = {port["port"] for port in service["spec"]["ports"]}
    pattern = re.compile(r"http://([a-z0-9-]+)\.([a-z0-9-]+)\.svc\.cluster\.local:(\d+)")
    references = 0
    for path in K8S.rglob("*.yaml"):
        for name, namespace, port in pattern.findall(path.read_text(encoding="utf-8")):
            references += 1
            assert int(port) in declared.get((name, namespace), set()), (path, name, namespace)
    assert references > 0


def test_mobile_bff_reaches_new_services_with_shared_internal_token() -> None:
    deployment = yaml.safe_load((K8S / "bff-movil" / "deployment.yaml").read_text(encoding="utf-8"))
    environment = {
        item["name"]: item
        for item in deployment["spec"]["template"]["spec"]["containers"][0]["env"]
    }
    for service in NEW_SERVICES:
        url = environment[f"{service.upper()}_SERVICE_URL"]["value"]
        assert url == f"http://{service}.{service}.svc.cluster.local:8080"
    assert environment["INTERNAL_API_TOKEN"]["valueFrom"]["secretKeyRef"] == {
        "name": "profiling-internal",
        "key": "INTERNAL_API_TOKEN",
    }


@pytest.mark.parametrize("service", ["notificaciones", "asistencias"])
def test_new_services_read_runtime_secret_and_migrate_with_separate_role(service: str) -> None:
    variable = f"{service.upper()}_DATABASE_URL"
    reference = {"secretKeyRef": {"name": f"{service}-runtime", "key": variable}}
    deployment = yaml.safe_load((K8S / service / "deployment.yaml").read_text(encoding="utf-8"))
    job = yaml.safe_load((K8S / service / "migrate-job.yaml").read_text(encoding="utf-8"))
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    environment = {item["name"]: item for item in container["env"]}
    assert environment[variable]["valueFrom"] == reference
    assert environment["INTERNAL_API_TOKEN"]["valueFrom"] == {
        "secretKeyRef": {"name": f"{service}-runtime", "key": "INTERNAL_API_TOKEN"}
    }
    migration = job["spec"]["template"]["spec"]["containers"][0]
    assert migration["command"] == ["alembic", "upgrade", "head"]
    assert migration["env"] == [
        {"name": "DB_RUNTIME_ROLE", "value": f"{service}_runtime"},
        {
            "name": variable,
            "valueFrom": {"secretKeyRef": {"name": f"{service}-migrator", "key": variable}},
        },
    ]
    assert job["metadata"]["name"] == f"{service}-migrate-${{MIGRATION_ID}}"
    migrations = (ROOT / "services" / service / "migrations" / "env.py").read_text(encoding="utf-8")
    assert f'os.environ["{variable}"]' in migrations
    assert "config.set_main_option(" not in migrations
    assert "sqlalchemy.url" not in (ROOT / "services" / service / "alembic.ini").read_text(
        encoding="utf-8"
    )


def test_notification_scheduler_calls_internal_dispatch_with_service_token() -> None:
    text = (K8S / "notificaciones" / "despachos-cronjob.yaml").read_text(encoding="utf-8")
    # envsubst reemplazaria cualquier otra referencia y alteraria el script.
    assert "$" not in text.replace("${IMAGE}", "")
    cronjob = yaml.safe_load(text)
    assert cronjob["spec"]["concurrencyPolicy"] == "Forbid"
    pod = cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    container = pod["containers"][0]
    script = container["command"][-1]
    compile(script, "despachos", "exec")
    assert "http://notificaciones.notificaciones.svc.cluster.local:8080/v1/despachos" in script
    app = (ROOT / "services" / "notificaciones" / "src" / "notificaciones" / "app.py").read_text(
        encoding="utf-8"
    )
    assert '@app.post("/v1/despachos")' in app
    deployment = yaml.safe_load(
        (K8S / "notificaciones" / "deployment.yaml").read_text(encoding="utf-8")
    )
    token = next(
        item
        for item in deployment["spec"]["template"]["spec"]["containers"][0]["env"]
        if item["name"] == "INTERNAL_API_TOKEN"
    )
    assert container["env"] == [token]


def test_claims_notification_scheduler_publishes_outbox_with_service_token() -> None:
    text = (K8S / "siniestros" / "notificaciones-cronjob.yaml").read_text(encoding="utf-8")
    # envsubst reemplazaria cualquier otra referencia y alteraria el script.
    assert "$" not in text.replace("${IMAGE}", "")
    cronjob = yaml.safe_load(text)
    assert cronjob["metadata"]["namespace"] == "siniestros"
    assert cronjob["spec"]["concurrencyPolicy"] == "Forbid"
    container = cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]
    script = container["command"][-1]
    compile(script, "publicacion", "exec")
    route = "/internal/v1/notificaciones/publicacion"
    assert f"http://siniestros.siniestros.svc.cluster.local:8080{route}" in script
    app = (ROOT / "services" / "siniestros" / "src" / "siniestros" / "app.py").read_text(
        encoding="utf-8"
    )
    assert f'@app.post("{route}")' in app
    deployment = yaml.safe_load(
        (K8S / "siniestros" / "deployment.yaml").read_text(encoding="utf-8")
    )
    environment = {
        item["name"]: item
        for item in deployment["spec"]["template"]["spec"]["containers"][0]["env"]
    }
    assert container["env"] == [environment["INTERNAL_API_TOKEN"]]
    assert (
        environment["NOTIFICACIONES_SERVICE_URL"]["value"]
        == "http://notificaciones.notificaciones.svc.cluster.local:8080"
    )
    deploy = (ROOT / "deploy" / "codebuild" / "deploy.sh").read_text(encoding="utf-8")
    assert 'envsubst < "${K8S_DIR}/notificaciones-cronjob.yaml"' in deploy
