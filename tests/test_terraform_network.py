import json
import os
import re
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PRIMARY = ROOT / "terraform" / "environments" / "prod" / "primary"
SERVICES = (
    "cotizacion",
    "bff-web",
    "bff-movil",
    "identidad",
    "perfilamiento",
    "polizas",
    "siniestros",
    "notificaciones",
    "asistencias",
)


def test_primary_consumes_all_precreated_groups_without_owning_network() -> None:
    config = "\n".join(path.read_text() for path in PRIMARY.glob("*.tf"))
    assert 'module "network" {' not in config
    assert "module.network." not in config
    for service in SERVICES:
        module = "cicd" if service == "cotizacion" else f"cicd_{service.replace('-', '_')}"
        body = re.search(rf'^module "{module}" \{{\n(.*?)^\}}', config, re.M | re.S)
        assert body is not None, module
        assert re.search(
            rf"existing_security_group_id\s*=\s*var.network_context.security_group_ids.cicd.{service}\s",
            body[1],
        ), service
    for module, group in (("database", "db"), ("redis", "redis")):
        body = re.search(rf'^module "{module}" \{{\n(.*?)^\}}', config, re.M | re.S)
        assert body is not None
        assert f"var.network_context.security_group_ids.{group}" in body[1]
    api = re.search(r'^module "api_gateway" \{\n(.*?)^\}', config, re.M | re.S)
    assert api is not None
    for group in ("alb", "vpc_link"):
        assert f"var.network_context.security_group_ids.{group}" in api[1]
    for module, resources in {
        "cicd": ("deploy",),
        "api-gateway": ("alb", "vpc_link"),
        "rds-postgres": ("db",),
        "elasticache-redis": ("redis",),
    }.items():
        source = (ROOT / "terraform" / "modules" / module / "main.tf").read_text()
        for resource in resources:
            assert re.search(
                rf"moved \{{\s+from\s*=\s*aws_security_group.{resource}\s+"
                rf"to\s*=\s*aws_security_group.{resource}\[0\]\s+\}}",
                source,
            ), (module, resource)


def test_network_context_metadata_fails_closed_offline(tmp_path: Path) -> None:
    terraform = shutil.which("terraform")
    if terraform is None or not os.environ.get("TF_CLI_CONFIG_FILE"):
        pytest.skip(
            "Requiere Terraform existente y TF_CLI_CONFIG_FILE con mirror offline explicito"
        )
    # Probar el contrato real sin cargar los recursos/secretos de primary.
    for name in ("variables.tf", "network-context.tf"):
        shutil.copyfile(PRIMARY / name, tmp_path / name)
    (tmp_path / "versions.tf").write_text(
        "terraform {\n  required_providers {\n    aws = {\n"
        '      source = "hashicorp/aws"\n      version = "~> 5.0"\n    }\n  }\n}\n'
    )
    context = {
        "account_id": "396383602233",
        "region": "us-east-2",
        "name_prefix": "solventa-prod",
        "vpc_id": "vpc-0123456789abcdef0",
        "vpc_arn": "arn:aws:ec2:us-east-2:396383602233:vpc/vpc-0123456789abcdef0",
        "public_subnet_ids": [f"subnet-{index:017x}" for index in (1, 2)],
        "private_subnet_ids": [f"subnet-{index:017x}" for index in (3, 4)],
        "security_group_ids": {
            "db": "sg-00000000000000001",
            "redis": "sg-00000000000000002",
            "alb": "sg-00000000000000003",
            "vpc_link": "sg-00000000000000004",
            "cicd": {service: f"sg-{index:017x}" for index, service in enumerate(SERVICES, 5)},
        },
    }
    groups = {
        "db": "solventa-prod-db",
        "redis": "solventa-prod-redis-sg",
        "alb": "solventa-prod-alb-internal",
        "vpc_link": "solventa-prod-vpclink",
        **{
            f"cicd-{service}": (
                "solventa-prod-codebuild-deploy"
                if service == "cotizacion"
                else f"solventa-prod-{service}-codebuild-deploy"
            )
            for service in SERVICES
        },
    }
    metadata = {
        "data.aws_vpc.network": {
            "owner_id": context["account_id"],
            "arn": context["vpc_arn"],
            "tags": {
                "Project": "solventa",
                "Component": "backend",
                "Environment": "prod",
                "Name": "solventa-prod-vpc",
            },
        },
        **{
            f'data.aws_security_group.network["{key}"]': {
                "arn": (
                    "arn:aws:ec2:us-east-2:396383602233:security-group/"
                    + (
                        context["security_group_ids"]["cicd"][key.removeprefix("cicd-")]
                        if key.startswith("cicd-")
                        else context["security_group_ids"][key]
                    )
                ),
                "vpc_id": context["vpc_id"],
                "name": name,
            }
            for key, name in groups.items()
        },
        **{
            f'data.aws_subnet.network["{id}"]': {
                "owner_id": context["account_id"],
                "vpc_id": context["vpc_id"],
                "availability_zone": f"us-east-2{'ab'[index]}",
                "tags": {"Tier": tier},
            }
            for tier in ("public", "private")
            for index, id in enumerate(context[f"{tier}_subnet_ids"])
        },
    }
    db = 'data.aws_security_group.network["db"]'
    subnet = f'data.aws_subnet.network["{context["private_subnet_ids"][1]}"]'
    boundaries = {
        family: f"arn:aws:iam::396383602233:policy/test-{family}-boundary"
        for family in ("network", "ci", "application")
    }
    cases = (
        ("valid", None, None, None),
        ("foreign_group_vpc", db, "vpc_id", "vpc-00000000000000000"),
        (
            "foreign_group_owner",
            db,
            "arn",
            "arn:aws:ec2:us-east-2:111122223333:security-group/sg-00000000000000001",
        ),
        ("swapped_group", db, "name", "solventa-prod-redis-sg"),
        ("foreign_subnet", subnet, "vpc_id", "vpc-00000000000000000"),
        ("wrong_tier", subnet, "tags", {"Tier": "public"}),
        ("single_az", subnet, "availability_zone", "us-east-2a"),
        ("jenkins_vpc", "data.aws_vpc.network", "tags", {"Name": "jenkins"}),
        ("foreign_context", "var.network_context", "account_id", "111122223333"),
        ("duplicate_group", "var.network_context", "duplicate_group", None),
        (
            "foreign_boundary",
            "var.permissions_boundary_arns",
            "network",
            "arn:aws:iam::111122223333:policy/test-boundary",
        ),
        ("empty_boundary", "var.permissions_boundary_arns", "ci", ""),
        (
            "managed_boundary",
            "var.permissions_boundary_arns",
            "application",
            "arn:aws:iam::aws:policy/ReadOnlyAccess",
        ),
        (
            "shared_boundary",
            "var.permissions_boundary_arns",
            "application",
            boundaries["ci"],
        ),
    )
    runs = ['mock_provider "aws" { override_during = plan }']
    for name, target, key, value in cases:
        inputs, responses = deepcopy(context), deepcopy(metadata)
        family_inputs = deepcopy(boundaries)
        if target == "var.network_context":
            if key == "duplicate_group":
                inputs["security_group_ids"]["db"] = inputs["security_group_ids"]["redis"]
            else:
                inputs[key] = value
        elif target == "var.permissions_boundary_arns":
            family_inputs[key] = value
        elif target:
            responses[target][key] = value
        variables = {
            "network_context": inputs,
            "account_id": context["account_id"],
            "aws_profile": "test-only",
            "permissions_boundary_arns": family_inputs,
            "public_access_cidrs": ["192.0.2.1/32"],
            "eks_admin_principal_arns": ["arn:aws:iam::396383602233:role/test-only"],
        }
        runs.append(f'run "{name}" {{\ncommand = plan\nvariables {{')
        runs.extend(f"{key} = {json.dumps(value)}" for key, value in variables.items())
        runs.append("}")
        for address, response in responses.items():
            runs.append(
                f"override_data {{\ntarget = {address}\nvalues = {json.dumps(response)}\n}}"
            )
        if target:
            failure = "data.aws_vpc.network" if name == "single_az" else target
            runs.append(f"expect_failures = [{failure}]")
        runs.append("}")
    (tmp_path / "network.tftest.hcl").write_text("\n".join(runs))
    for args in (
        ("init", "-backend=false", "-input=false", "-no-color"),
        ("test", "-no-color"),
    ):
        result = subprocess.run(
            [terraform, f"-chdir={tmp_path}", *args],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        (tmp_path / f"{args[0]}.txt").write_text(result.stdout + result.stderr)
        assert result.returncode == 0, result.stdout + result.stderr
    assert f"Success! {len(cases)} passed, 0 failed." in result.stdout
