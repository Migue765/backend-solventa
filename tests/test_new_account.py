import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from scripts.render_new_account import render

ROOT = Path(__file__).resolve().parents[1]
ENVIRONMENT = ROOT / "terraform" / "environments" / "nueva-cuenta"
SERVICES = ("identidad", "perfilamiento", "bff-web")
ACCOUNT = "096571798698"
VPC = "vpc-00000000000000001"


def deployment_inputs() -> tuple[dict[str, Any], dict[str, str]]:
    context: dict[str, Any] = {
        "account_id": ACCOUNT,
        "region": "us-east-2",
        "cluster_name": "solventa-nueva-eks",
        "api_endpoint": "https://synthetic.execute-api.us-east-2.amazonaws.com",
        "jwt_issuer": "https://synthetic.execute-api.us-east-2.amazonaws.com",
        "jwt_audience": "solventa-api",
        "identidad_role_arn": f"arn:aws:iam::{ACCOUNT}:role/solventa-nueva-identidad",
        "ecr_repositories": {
            service: f"{ACCOUNT}.dkr.ecr.us-east-2.amazonaws.com/solventa/{service}"
            for service in SERVICES
        },
        "target_group_arns": {
            service: (
                f"arn:aws:elasticloadbalancing:us-east-2:{ACCOUNT}:"
                f"targetgroup/solventa-nueva-{service}/0123456789abcdef"
            )
            for service in ("identidad", "bff-web")
        },
    }
    return context, {
        service: f"{repository}@sha256:{'a' * 64}"
        for service, repository in context["ecr_repositories"].items()
    }


def test_render_prepares_existing_services_without_opening_login() -> None:
    context, images = deployment_inputs()
    manifests = render(context, images)
    for service in SERVICES:
        seed = manifests[f"{service}/seed.json"]["items"]
        assert not any(
            item["kind"] in {"HorizontalPodAutoscaler", "TargetGroupBinding"} for item in seed
        )
        deployment = next(item for item in seed if item["kind"] == "Deployment")
        assert deployment["spec"]["replicas"] == 0
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        assert container["image"] == images[service]
        if service != "perfilamiento":
            environment = {entry["name"]: entry for entry in container["env"]}
            assert environment["JWT_ISSUER"]["value"] == context["jwt_issuer"]
            assert environment["ENCRYPTED_LOGIN_ENABLED"]["value"] == "false"
        if service != "bff-web":
            job = manifests[f"{service}/migration.json"]
            job_env = job["spec"]["template"]["spec"]["containers"][0]["env"]
            assert any(
                item.get("valueFrom", {}).get("secretKeyRef", {}).get("name")
                == f"{service}-migrator"
                for item in job_env
            )
    assert "bff-web/migration.json" not in manifests
    assert {name for name in manifests if name.startswith("bindings/")} == {
        "bindings/identidad.json",
        "bindings/bff-web.json",
    }
    for service in ("identidad", "bff-web"):
        assert (
            manifests[f"bindings/{service}.json"]["spec"]["targetGroupARN"]
            == context["target_group_arns"][service]
        )


def test_optional_cotizacion_uses_real_schema_and_no_identity_irsa() -> None:
    context, images = deployment_inputs()
    repository = f"{ACCOUNT}.dkr.ecr.us-east-2.amazonaws.com/solventa/cotizacion"
    context["ecr_repositories"]["cotizacion"] = repository
    images["cotizacion"] = f"{repository}@sha256:{'b' * 64}"
    target = (
        f"arn:aws:elasticloadbalancing:us-east-2:{ACCOUNT}:"
        "targetgroup/solventa-nueva-cotizacion/0123456789abcdef"
    )
    context["target_group_arns"]["cotizacion"] = target
    manifests = render(context, images)
    seed = manifests["cotizacion/seed.json"]["items"]
    account = next(item for item in seed if item["kind"] == "ServiceAccount")
    assert "eks.amazonaws.com/role-arn" not in account["metadata"].get("annotations", {})
    assert account["automountServiceAccountToken"] is False
    deployment = next(item for item in seed if item["kind"] == "Deployment")
    assert deployment["spec"]["replicas"] == 0
    env = deployment["spec"]["template"]["spec"]["containers"][0]["env"]
    assert (
        next(item["value"] for item in env if item["name"] == "COTIZACION_PERFILAMIENTO_HABILITADO")
        == "true"
    )
    migration = manifests["cotizacion/migration.json"]["spec"]["template"]["spec"]["containers"][0]
    assert any(item.get("value") == "cotizacion_runtime" for item in migration["env"])
    assert "bindings/cotizacion.json" not in manifests


@pytest.mark.parametrize("service", ["polizas", "notificaciones"])
def test_optional_domain_stays_private_and_separates_migration_credentials(service: str) -> None:
    context, images = deployment_inputs()
    repository = f"{ACCOUNT}.dkr.ecr.us-east-2.amazonaws.com/solventa/{service}"
    context["ecr_repositories"][service] = repository
    images[service] = f"{repository}@sha256:{'c' * 64}"
    manifests = render(context, images)
    deployment = next(
        item for item in manifests[f"{service}/seed.json"]["items"] if item["kind"] == "Deployment"
    )
    assert deployment["spec"]["replicas"] == 0
    spec = deployment["spec"]["template"]["spec"]
    assert spec["automountServiceAccountToken"] is False
    runtime = spec["containers"][0]["env"]
    assert any(
        item.get("valueFrom", {}).get("secretKeyRef", {}).get("name") == f"{service}-runtime"
        for item in runtime
    )
    migration = manifests[f"{service}/migration.json"]["spec"]["template"]["spec"]["containers"][0]
    assert any(
        item.get("valueFrom", {}).get("secretKeyRef", {}).get("name") == f"{service}-migrator"
        for item in migration["env"]
    )
    assert f"bindings/{service}.json" not in manifests


@pytest.mark.parametrize(
    "case",
    ["old-account", "wrong-issuer", "plaintext", "wrong-api", "missing-api", "swapped-tg"],
)
def test_render_rejects_wrong_destination_or_image(case: str) -> None:
    context, images = deployment_inputs()
    if case == "old-account":
        context["account_id"] = "396383602233"
    elif case == "wrong-issuer":
        context["jwt_issuer"] = "https://previous.invalid"
    elif case == "plaintext":
        context["api_endpoint"] = context["api_endpoint"].replace("https:", "http:")
    elif case == "wrong-api":
        images["identidad"] = images["bff-web"]
    elif case == "missing-api":
        del images["perfilamiento"]
    else:
        context["target_group_arns"]["identidad"] = context["target_group_arns"]["bff-web"]
    with pytest.raises(ValueError):
        render(context, images)


def mock_configuration() -> str:
    documents = {
        "aws_caller_identity": {
            "account_id": ACCOUNT,
            "arn": f"arn:aws:sts::{ACCOUNT}:assumed-role/test-deploy/session",
            "user_id": "synthetic-session",
        },
        "aws_region": {"name": "us-east-2"},
        "aws_partition": {"partition": "aws"},
        "aws_availability_zones": {"names": ["us-east-2a", "us-east-2b"]},
        "aws_iam_policy_document": {"json": '{"Version":"2012-10-17","Statement":[]}'},
    }
    sections = [
        f'mock_data "{kind}" {{ defaults = {json.dumps(values)} }}'
        for kind, values in documents.items()
    ]
    return "\n".join(sections)


def primary_overrides() -> str:
    overrides: dict[str, dict[str, Any]] = {
        "data.aws_vpc.network": {
            "owner_id": ACCOUNT,
            "arn": f"arn:aws:ec2:us-east-2:{ACCOUNT}:vpc/{VPC}",
            "default": False,
            "tags": {"Name": "solventa-nueva-vpc", "Component": "backend"},
        },
    }
    for index in range(1, 5):
        overrides[f'data.aws_subnet.network["subnet-{index:017x}"]'] = {
            "owner_id": ACCOUNT,
            "vpc_id": VPC,
            "availability_zone": "us-east-2a" if index % 2 else "us-east-2b",
            "tags": {"Tier": "public" if index <= 2 else "private"},
        }
    groups = {
        "db": "solventa-nueva-db",
        "redis": "solventa-nueva-redis-sg",
        "alb": "solventa-nueva-alb-internal",
        "vpc_link": "solventa-nueva-vpclink",
        **{
            f"cicd-{service}": f"solventa-nueva-{service}-codebuild-deploy"
            for service in (
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
        },
    }
    for index, (key, name) in enumerate(groups.items(), 1):
        overrides[f'data.aws_security_group.network["{key}"]'] = {
            "arn": f"arn:aws:ec2:us-east-2:{ACCOUNT}:security-group/sg-{index:017x}",
            "vpc_id": VPC,
            "name": name,
        }
    return "\n".join(
        f"override_data {{\n target = {target}\n values = {json.dumps(values)}\n}}"
        for target, values in overrides.items()
    )


def run_terraform(command: list[str], cwd: Path, environment: dict[str, str]) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if result.returncode:
        diagnostics = []
        for line in result.stdout.splitlines():
            if line.startswith("{"):
                event = json.loads(line)
                if event.get("type") == "diagnostic":
                    diagnostics.append(str(event["diagnostic"]))
        pytest.fail("\n".join(diagnostics) + result.stderr or result.stdout[-3000:])
    return result.stdout


def prepare_root(name: str, tmp_path: Path) -> tuple[str, Path, dict[str, str]]:
    terraform = shutil.which("terraform")
    mirror = ENVIRONMENT / name / ".terraform" / "providers"
    if not mirror.exists() and os.environ.get("TF_DATA_DIR"):
        mirror = Path(os.environ["TF_DATA_DIR"]) / "providers"
    if terraform is None or not mirror.exists():
        pytest.skip("Requiere Terraform y providers previamente inicializados sin backend.")
    providers = [
        line.split('"')[1]
        for line in (ENVIRONMENT / name / ".terraform.lock.hcl").read_text().splitlines()
        if line.startswith("provider ")
    ]
    if any(not mirror.joinpath(*provider.split("/")).exists() for provider in providers):
        pytest.skip("El mirror disponible no contiene todos los providers de este root.")
    checkout = tmp_path / "checkout"
    root = checkout / "terraform" / "environments" / "nueva-cuenta" / name
    shutil.copytree(ENVIRONMENT / name, root, ignore=shutil.ignore_patterns(".terraform"))
    shutil.copytree(ROOT / "terraform" / "modules", checkout / "terraform" / "modules")
    config = tmp_path / "offline.tfrc"
    config.write_text(
        "provider_installation {\n filesystem_mirror {\n"
        f" path = {json.dumps(str(mirror))}\n"
        ' include = ["registry.terraform.io/hashicorp/*"]\n }\n}\n',
        encoding="utf-8",
    )
    environment = {
        **os.environ,
        "TF_CLI_CONFIG_FILE": str(config),
        "TF_DATA_DIR": str(tmp_path / "terraform-data"),
        "AWS_EC2_METADATA_DISABLED": "true",
        "CHECKPOINT_DISABLE": "1",
    }
    (root / "tests").mkdir(exist_ok=True)
    return terraform, root, environment


def test_integrated_primary_mock_plan(tmp_path: Path) -> None:
    terraform, primary, environment = prepare_root("primary", tmp_path)
    mock = mock_configuration()
    (primary / "tests" / "aws.tfmock.hcl").write_text(mock, encoding="utf-8")
    (primary / "tests" / "overrides.tftest.hcl").write_text(
        'mock_provider "aws" { source = "./tests" }\n'
        'mock_provider "tls" {}\nmock_provider "random" {}\n'
        + primary_overrides()
        + '\nrun "integrated_topology" { command = plan }\n'
        + """
run "medium_transition" {
  command = plan
  variables {
    enable_medium_node_group = true
    medium_node_release_version = "1.35.9-20261119"
  }
}
run "four_small" {
  command = plan
  variables {
    enable_small_node_group = true
    small_node_release_version = "1.35.9-20261119"
  }
}
run "remaining_runtime_assets" {
  command = plan
  variables {
    enable_siniestros_evidence = true
    bootstrap_build_sources = {
      polizas = "$DIGEST"
      notificaciones = "$DIGEST"
      siniestros = "$DIGEST"
      asistencias = "$DIGEST"
      bff-movil = "$DIGEST"
    }
  }
}
run "reject_duplicate_pipeline_build" {
  command = plan
  variables { bootstrap_build_sources = { identidad = "$DIGEST" } }
  expect_failures = [var.bootstrap_build_sources]
}
run "reject_unknown_bootstrap_api" {
  command = plan
  variables { bootstrap_build_sources = { desconocida = "$DIGEST" } }
  expect_failures = [var.bootstrap_build_sources]
}
run "reject_small_without_release" {
  command = plan
  variables { enable_small_node_group = true }
  expect_failures = [var.small_node_release_version]
}
run "reject_mixed_expansion" {
  command = plan
  variables {
    enable_small_node_group = true
    small_node_release_version = "1.35.9-20261119"
    enable_medium_node_group = true
    medium_node_release_version = "1.35.9-20261119"
  }
  expect_failures = [var.enable_small_node_group]
}
run "reject_medium_without_release" {
  command = plan
  variables { enable_medium_node_group = true }
  expect_failures = [var.medium_node_release_version]
}
run "reject_extra_transition_nodes" {
  command = plan
  variables {
    enable_medium_node_group = true
    medium_node_release_version = "1.35.9-20261119"
    node_count = 3
  }
  expect_failures = [var.enable_medium_node_group]
}
run "reject_common_boundary" {
  command = plan
  variables {
    permissions_boundary_arns = {
      network = "arn:aws:iam::096571798698:policy/common"
      ci = "arn:aws:iam::096571798698:policy/common"
      application = "arn:aws:iam::096571798698:policy/common"
    }
  }
  expect_failures = [var.permissions_boundary_arns]
}
run "reject_public_management" {
  command = plan
  variables { public_access_cidrs = ["0.0.0.0/0"] }
  expect_failures = [var.public_access_cidrs]
}
run "reject_https_without_server_name" {
  command = plan
  variables {
    api_listener_protocol = "HTTPS"
    api_certificate_arn = format(
      "arn:aws:acm:us-east-2:096571798698:certificate/%s",
      "00000000-0000-0000-0000-000000000001"
    )
  }
  expect_failures = [terraform_data.tls_configuration]
}
run "reject_unprepared_pipeline" {
  command = plan
  variables { pipeline_services = ["siniestros"] }
  expect_failures = [var.pipeline_services]
}
run "reject_default_vpc" {
  command = plan
  override_data {
    target = data.aws_vpc.network
    values = {
      owner_id = "$ACCOUNT"
      arn = "arn:aws:ec2:us-east-2:$ACCOUNT:vpc/$VPC"
      default = true
      tags = { Name = "solventa-nueva-vpc", Component = "backend" }
    }
  }
  expect_failures = [data.aws_vpc.network]
}
run "reject_wrong_group_name" {
  command = plan
  override_data {
    target = data.aws_security_group.network["redis"]
    values = {
      arn = "arn:aws:ec2:us-east-2:$ACCOUNT:security-group/sg-00000000000000002"
      vpc_id = "$VPC"
      name = "unrelated-redis"
    }
  }
  expect_failures = [data.aws_security_group.network["redis"]]
}
""".replace("$ACCOUNT", ACCOUNT)
        .replace("$VPC", VPC)
        .replace("$DIGEST", "a" * 64),
        encoding="utf-8",
    )
    existing = primary / "tests" / "ecr.tftest.hcl"
    existing.write_text(
        existing.read_text().replace(
            'mock_provider "aws" {}', 'mock_provider "aws" { source = "./tests" }'
        )
        + "\n"
        + primary_overrides(),
        encoding="utf-8",
    )
    run_terraform(
        [terraform, "init", "-backend=false", "-input=false", "-no-color"], primary, environment
    )
    output = run_terraform(
        [terraform, "test", "-json", "-verbose", "-var-file=tests/fixtures.tfvars.example"],
        primary,
        environment,
    )
    plans = [
        event["test_plan"]
        for line in output.splitlines()
        if (event := json.loads(line)).get("type") == "test_plan"
        and event.get("@testrun") == "integrated_topology"
    ]
    assert len(plans) == 1
    changes = plans[0]["resource_changes"]
    resources = {
        item["address"]: item["change"]["after"] for item in changes if item["mode"] == "managed"
    }

    def by_type(kind: str) -> list[dict[str, Any]]:
        return [
            item["change"]["after"]
            for item in changes
            if item["mode"] == "managed" and item["type"] == kind
        ]

    assert len(by_type("aws_ecr_repository")) == 9
    assert len(by_type("aws_db_instance")) == 1
    assert by_type("aws_db_instance")[0]["db_name"] == "solventa_prod"
    assert not by_type("aws_db_instance")[0]["publicly_accessible"]
    assert by_type("aws_lb")[0]["internal"]
    assert len(by_type("aws_codepipeline")) == 3
    for pipeline in by_type("aws_codepipeline"):
        assert not pipeline["trigger"]
        assert any(stage["name"] == "Approve" for stage in pipeline["stage"])
    assert len(by_type("aws_iam_role")) == 13
    for role in by_type("aws_iam_role"):
        assert role["permissions_boundary"].startswith(f"arn:aws:iam::{ACCOUNT}:policy/test-")
    cors = by_type("aws_apigatewayv2_api")[0]["cors_configuration"][0]
    assert cors["allow_origins"] == ["http://localhost:4200"]
    assert not cors["allow_credentials"]
    assert set(cors["allow_methods"]) == {"GET", "POST", "PUT", "DELETE", "OPTIONS"}
    assert set(cors["allow_headers"]) == {
        "authorization",
        "content-type",
        "idempotency-key",
        "api-version",
        "upload-offset",
        "x-correlation-id",
    }
    assert set(cors["expose_headers"]) == {
        "date",
        "retry-after",
        "x-reporte-huella",
        "x-content-sha256",
    }
    routes = {route["route_key"]: route for route in by_type("aws_apigatewayv2_route")}
    for path in ("/v1/sesiones/desafio", "/v1/usuarios/desafio"):
        assert routes[f"GET {path}"]["authorization_type"] == "NONE"
    assert routes["ANY /v1/{proxy+}"]["authorization_type"] == "JWT"
    assert resources["module.eks.aws_eks_node_group.default"]["scaling_config"][0] == {
        "min_size": 2,
        "desired_size": 2,
        "max_size": 2,
    }
    assert len(by_type("aws_eks_node_group")) == 1
    runtime_plan = next(
        event["test_plan"]
        for line in output.splitlines()
        if (event := json.loads(line)).get("type") == "test_plan"
        and event.get("@testrun") == "remaining_runtime_assets"
    )
    runtime_resources = {
        item["address"]: item["change"]["after"]
        for item in runtime_plan["resource_changes"]
        if item["mode"] == "managed"
    }
    for service in ("polizas", "notificaciones", "siniestros", "asistencias", "bff-movil"):
        build = runtime_resources[f'aws_codebuild_project.bootstrap_build["{service}"]']
        assert build["source"][0]["location"].endswith(f"/bootstrap-source/{'a' * 64}.zip")
        role = runtime_resources[f'aws_iam_role.bootstrap_build["{service}"]']
        assert role["permissions_boundary"] == f"arn:aws:iam::{ACCOUNT}:policy/test-ci"
    assert "module.siniestros_evidence[0].aws_s3_bucket.this" in runtime_resources
    assert not any(
        name.startswith(("module.siniestros_evidence", "module.siniestros_irsa"))
        for name in resources
    )
    runtime_role = runtime_resources["module.siniestros_irsa[0].aws_iam_role.this"]
    assert runtime_role["name"] == "solventa-nueva-siniestros"
    assert runtime_role["permissions_boundary"] == f"arn:aws:iam::{ACCOUNT}:policy/test-application"
    assert (
        sum(item["type"] == "aws_eks_node_group" for item in runtime_plan["resource_changes"]) == 1
    )
    medium_plan = next(
        event["test_plan"]
        for line in output.splitlines()
        if (event := json.loads(line)).get("type") == "test_plan"
        and event.get("@testrun") == "medium_transition"
    )
    groups = {
        item["address"]: item["change"]["after"]
        for item in medium_plan["resource_changes"]
        if item["type"] == "aws_eks_node_group"
    }
    assert set(groups) == {
        "module.eks.aws_eks_node_group.default",
        'module.eks.aws_eks_node_group.additional["medium"]',
    }
    legacy = groups["module.eks.aws_eks_node_group.default"]
    medium = groups['module.eks.aws_eks_node_group.additional["medium"]']
    assert legacy["instance_types"] == ["t3.small"]
    assert medium["instance_types"] == ["t3.medium"]
    assert medium["scaling_config"] == legacy["scaling_config"]
    assert medium["release_version"] == "1.35.9-20261119"
    small_plan = next(
        event["test_plan"]
        for line in output.splitlines()
        if (event := json.loads(line)).get("type") == "test_plan"
        and event.get("@testrun") == "four_small"
    )
    small_groups = {
        item["address"]: item["change"]["after"]
        for item in small_plan["resource_changes"]
        if item["type"] == "aws_eks_node_group"
    }
    assert set(small_groups) == {
        "module.eks.aws_eks_node_group.default",
        'module.eks.aws_eks_node_group.additional["small-extra"]',
    }
    for group in small_groups.values():
        assert group["instance_types"] == ["t3.small"]
        assert group["scaling_config"] == legacy["scaling_config"]
    assert (
        small_groups['module.eks.aws_eks_node_group.additional["small-extra"]']["release_version"]
        == "1.35.9-20261119"
    )


@pytest.mark.parametrize("name", ["bootstrap", "network", "addons"])
def test_other_roots_keep_account_guard_and_separate_responsibilities(
    name: str, tmp_path: Path
) -> None:
    terraform, root, environment = prepare_root(name, tmp_path)
    mock = mock_configuration()
    variables: dict[str, Any] = {
        "aws_profile": "synthetic-role-profile",
        "deployment_role_arn": f"arn:aws:iam::{ACCOUNT}:role/test-deploy",
    }
    if name == "addons":
        variables = {
            "cluster_addons": {
                "account_id": ACCOUNT,
                "aws_region": "us-east-2",
                "aws_profile": "synthetic-role-profile",
                "deployment_role_arn": f"arn:aws:iam::{ACCOUNT}:role/test-deploy",
                "cluster_name": "solventa-nueva-eks",
                "vpc_id": VPC,
                "alb_controller_role_arn": f"arn:aws:iam::{ACCOUNT}:role/test-alb",
            }
        }
        cluster = {
            "arn": f"arn:aws:eks:us-east-2:{ACCOUNT}:cluster/solventa-nueva-eks",
            "endpoint": "https://synthetic.invalid",
            "certificate_authority": [{"data": "dGVzdA=="}],
            "vpc_config": [{"vpc_id": VPC}],
        }
        mock += f'\nmock_data "aws_eks_cluster" {{ defaults = {json.dumps(cluster)} }}'
    (root / "tests" / "aws.tfmock.hcl").write_text(mock, encoding="utf-8")
    (root / "tests" / "identity.tftest.hcl").write_text(
        'mock_provider "aws" { source = "./tests" }\n'
        + ('mock_provider "helm" {}\n' if name == "addons" else "")
        + "variables {\n"
        + "\n".join(f"{key} = {json.dumps(value)}" for key, value in variables.items())
        + """
}
run "valid" { command = plan }
run "reject_direct_user" {
  command = plan
  override_data {
    target = data.aws_caller_identity.deployment
    values = { arn = "arn:aws:iam::096571798698:user/admin" }
  }
  expect_failures = [data.aws_caller_identity.deployment]
}
""",
        encoding="utf-8",
    )
    run_terraform(
        [terraform, "init", "-backend=false", "-input=false", "-no-color"], root, environment
    )
    output = run_terraform([terraform, "test", "-json", "-verbose"], root, environment)
    plans = [
        event["test_plan"]
        for line in output.splitlines()
        if (event := json.loads(line)).get("type") == "test_plan"
        and event.get("@testrun") == "valid"
    ]
    assert len(plans) == 1
    counts: dict[str, int] = {}
    for item in plans[0]["resource_changes"]:
        if item["mode"] == "managed":
            counts[item["type"]] = counts.get(item["type"], 0) + 1
    if name == "bootstrap":
        assert counts["aws_s3_bucket"] == 1
        assert counts["aws_s3_bucket_versioning"] == 1
        assert "aws_eks_cluster" not in counts
    elif name == "network":
        assert counts["aws_vpc"] == 1
        assert counts["aws_subnet"] == 4
        assert counts["aws_security_group"] == 13
        assert counts["aws_nat_gateway"] == 1
    else:
        assert counts == {"helm_release": 2}
