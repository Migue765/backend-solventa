"""Render the existing first-journey manifests without contacting AWS or Kubernetes."""

import argparse
import json
import re
from pathlib import Path
from string import Template
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
SERVICES = ("identidad", "perfilamiento", "bff-web")
OPTIONAL_SERVICES = ("cotizacion", "polizas", "notificaciones")
ACCOUNT = "096571798698"
REGION = "us-east-2"


def load_manifest(directory: Path, values: dict[str, str], name: str) -> dict[str, Any]:
    text = Template((directory / name).read_text(encoding="utf-8")).substitute(values)
    manifest: dict[str, Any] = yaml.safe_load(text)
    if not isinstance(manifest, dict):
        raise ValueError(f"El manifiesto {name} no contiene un objeto Kubernetes.")
    return manifest


def render(context: dict[str, Any], images: dict[str, str]) -> dict[str, dict[str, Any]]:
    if not isinstance(context, dict) or not isinstance(images, dict):
        raise ValueError("Contexto e imagenes deben ser objetos JSON.")
    if (
        context.get("account_id") != ACCOUNT
        or context.get("region") != REGION
        or context.get("jwt_audience") != "solventa-api"
        or not re.fullmatch(
            r"https://[a-z0-9]+\.execute-api\.us-east-2\.amazonaws\.com",
            context.get("api_endpoint", ""),
        )
        or context.get("jwt_issuer") != context.get("api_endpoint")
    ):
        raise ValueError("Cuenta, region, endpoint o contrato JWT incorrectos.")
    if not set(SERVICES) <= set(images) or set(images) - set(SERVICES + OPTIONAL_SERVICES):
        raise ValueError(
            "Requeridos: Identidad, Perfilamiento y BFF Web; dominios opcionales no preparados."
        )
    cluster = context.get("cluster_name", "")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9-]*-eks", cluster):
        raise ValueError("El contexto debe incluir el nombre del cluster destino.")
    prefix = cluster.removesuffix("-eks")
    if context.get("identidad_role_arn") != f"arn:aws:iam::{ACCOUNT}:role/{prefix}-identidad":
        raise ValueError("El rol IRSA de Identidad no pertenece al destino.")

    rendered: dict[str, dict[str, Any]] = {}
    for service in SERVICES + tuple(name for name in OPTIONAL_SERVICES if name in images):
        repository = context["ecr_repositories"][service]
        expected = f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com/solventa/{service}"
        if repository != expected or not re.fullmatch(
            re.escape(expected) + r"@sha256:[0-9a-f]{64}", images[service]
        ):
            raise ValueError(f"La imagen de {service} debe usar su ECR nuevo y digest SHA256.")
        values = {
            "IMAGE": images[service],
            "MIGRATION_ID": images[service].rsplit(":", 1)[1][:12],
            "AWS_REGION": REGION,
            "JWT_ISSUER": context["jwt_issuer"],
            "JWT_AUDIENCE": context["jwt_audience"],
            "IRSA_ROLE_ARN": context["identidad_role_arn"] if service == "identidad" else "",
            "LOGIN_ENCRYPTION_SECRET_NAME": "identidad-login-encryption",
        }
        directory = ROOT / "deploy" / "k8s"
        if service != "cotizacion":
            directory /= service

        deployment = load_manifest(directory, values, "deployment.yaml")
        deployment["spec"]["replicas"] = 0
        if service == "cotizacion":
            for dependency in ("polizas", "notificaciones"):
                if dependency in images:
                    deployment["spec"]["template"]["spec"]["containers"][0]["env"].append(
                        {
                            "name": f"{dependency.upper()}_SERVICE_URL",
                            "value": f"http://{dependency}.{dependency}.svc.cluster.local:8080",
                        }
                    )
        seed = [
            load_manifest(directory, values, name) for name in ("namespace.yaml", "service.yaml")
        ]
        if (directory / "serviceaccount.yaml").exists():
            account = load_manifest(directory, values, "serviceaccount.yaml")
            if service == "cotizacion":
                account["metadata"].get("annotations", {}).pop("eks.amazonaws.com/role-arn", None)
                account["automountServiceAccountToken"] = False
            seed.append(account)
        seed.append(deployment)
        rendered[f"{service}/seed.json"] = {"apiVersion": "v1", "kind": "List", "items": seed}

        after = []
        for name in (
            "hpa.yaml",
            "pdb.yaml",
            "consent-outbox-cronjob.yaml",
            "despachos-cronjob.yaml",
        ):
            if (directory / name).exists():
                after.append(load_manifest(directory, values, name))
        if after:
            rendered[f"{service}/post-migration.json"] = {
                "apiVersion": "v1",
                "kind": "List",
                "items": after,
            }
        if service != "bff-web":
            rendered[f"{service}/migration.json"] = load_manifest(
                directory, values, "migrate-job.yaml"
            )
        if service in ("identidad", "bff-web"):
            target = context["target_group_arns"][service]
            expected_target = (
                f"arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT}:targetgroup/{prefix}-{service}/"
            )
            if not re.fullmatch(re.escape(expected_target) + r"[0-9a-f]+", target):
                raise ValueError(f"El target group de {service} no pertenece al destino.")
            values["TARGET_GROUP_ARN"] = target
            rendered[f"bindings/{service}.json"] = load_manifest(
                directory, values, "targetgroupbinding.yaml"
            )
    return rendered


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--images", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        manifests = render(
            json.loads(args.context.read_text(encoding="utf-8-sig")),
            json.loads(args.images.read_text(encoding="utf-8-sig")),
        )
        args.output.mkdir(parents=True, exist_ok=False)
        for relative, manifest in manifests.items():
            path = args.output / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as error:
        parser.exit(1, f"No se prepararon todos los manifiestos: {error}\n")


if __name__ == "__main__":
    main()
