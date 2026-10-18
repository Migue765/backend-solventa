"""Stage production profiling or retire only the four accepted HU11 deployments."""

import argparse
import base64
import json
import subprocess
from pathlib import Path
from typing import Any

CONTEXT = "arn:aws:eks:us-east-2:976083331509:cluster/solventa-prod-eks"
LEGACY = "hu11-perfilamiento"
TARGET = "perfilamiento"
WORKLOADS = ("bff-web", "cotizacion", "identidad", "perfilamiento")
KEYS = ("INTERNAL_API_TOKEN", "PERFILAMIENTO_DATABASE_URL")
URL = "http://perfilamiento.perfilamiento.svc.cluster.local:8080"
ROOT = Path(__file__).resolve().parents[1]


def kubectl(*args: str, payload: str | None = None) -> str:
    result = subprocess.run(
        ["kubectl", "--context", CONTEXT, *args],
        input=payload,
        capture_output=True,
        text=True,
        check=False,
        timeout=360,
    )
    if result.returncode:
        # API errors can include the submitted Secret; never echo command output.
        raise RuntimeError("kubectl fallo; inspeccione estado/eventos sin volcar secretos.")
    return result.stdout


def resource(*args: str) -> dict[str, Any]:
    value = json.loads(kubectl(*args, "-o", "json"))
    if not isinstance(value, dict):
        raise RuntimeError("Respuesta Kubernetes inesperada.")
    return value


def apply(value: dict[str, Any]) -> None:
    kubectl("apply", "-f", "-", payload=json.dumps(value))


def ready() -> None:
    kubectl("-n", TARGET, "rollout", "status", "deployment/perfilamiento", "--timeout=300s")
    endpoints = resource("-n", TARGET, "get", "endpoints", TARGET)
    if not any(subset.get("addresses") for subset in endpoints.get("subsets", [])):
        raise RuntimeError("Perfilamiento no tiene endpoints listos.")


def stage() -> None:
    source = resource("-n", LEGACY, "get", "deployment", TARGET)
    pod_spec = source["spec"]["template"]["spec"]
    account = resource(
        "-n", LEGACY, "get", "serviceaccount", pod_spec.get("serviceAccountName", "default")
    )
    if account["metadata"].get("annotations", {}).get("eks.amazonaws.com/role-arn"):
        raise RuntimeError("IRSA detectado: requiere preservar el binding antes de migrar.")
    if pod_spec.get("volumes") or pod_spec.get("imagePullSecrets"):
        raise RuntimeError("Volumen/credencial adicional detectado: requiere migracion explicita.")
    containers = pod_spec["containers"]
    if len(containers) != 1 or containers[0]["name"] != TARGET:
        raise RuntimeError("Configuracion de contenedores inesperada.")
    expected_env = {
        "INTERNAL_API_TOKEN": {
            "name": "INTERNAL_API_TOKEN",
            "valueFrom": {"secretKeyRef": {"name": "hu11-runtime", "key": KEYS[0]}},
        },
        "PERFILAMIENTO_DATABASE_URL": {
            "name": "PERFILAMIENTO_DATABASE_URL",
            "valueFrom": {"secretKeyRef": {"name": "hu11-runtime", "key": KEYS[1]}},
        },
        "SERVICE_APP": {"name": "SERVICE_APP", "value": "perfilamiento.app:create_app()"},
        "IDENTIDAD_SERVICE_URL": {
            "name": "IDENTIDAD_SERVICE_URL",
            "value": "http://identidad.identidad.svc.cluster.local:8080",
        },
    }
    if {item["name"]: item for item in containers[0]["env"]} != expected_env:
        raise RuntimeError("Variables de origen cambiaron: revisar antes de migrar.")
    pods = resource("-n", LEGACY, "get", "pods", "-l", "app=hu11-perfilamiento")
    images = {
        status["imageID"].removeprefix("docker-pullable://")
        for pod in pods["items"]
        for status in pod.get("status", {}).get("containerStatuses", [])
        if status["name"] == TARGET and status.get("ready")
    }
    if len(images) != 1 or "@sha256:" not in next(iter(images)):
        raise RuntimeError("No hay una unica imagen saludable fijable por digest.")
    image = images.pop()
    original = resource("-n", LEGACY, "get", "secret", "hu11-runtime")
    data = {key: original["data"][key] for key in KEYS}
    database = base64.b64decode(data[KEYS[1]], validate=True).decode()
    if not database.startswith(("postgresql://", "postgresql+", "postgres://")):
        raise RuntimeError("Backing store no compartido: no se permite almacenamiento local.")
    existing = kubectl(
        "-n", TARGET, "get", "secret", "perfilamiento-runtime", "--ignore-not-found", "-o", "json"
    )
    if existing and json.loads(existing).get("data") != data:
        raise RuntimeError("Secreto destino diferente: no se sobrescribe.")
    directory = ROOT / "deploy" / "k8s" / TARGET
    kubectl("apply", "-f", str(directory / "namespace.yaml"))
    apply(
        {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {"name": "perfilamiento-runtime", "namespace": TARGET},
            "type": "Opaque",
            "data": data,
        }
    )
    # Limit substitution to IMAGE; credentials only travel over stdin to Kubernetes.
    manifest = (directory / "deployment.yaml").read_text().replace("${IMAGE}", image)
    kubectl("apply", "-f", "-", payload=manifest)
    kubectl("apply", "-f", str(directory / "service.yaml"))
    ready()
    print(f"Perfilamiento listo con {image}; HU11 y Cotizacion sin cambios.")


def retire(*, acceptance_confirmed: bool) -> None:
    if not acceptance_confirmed:
        raise RuntimeError("Falta aceptacion del smoke coordinado y BFF/Cotizacion estables.")
    ready()
    deployments = resource("get", "deployments", "-A")["items"]
    cotizacion = next(
        item
        for item in deployments
        if item["metadata"]["namespace"] == "cotizacion"
        and item["metadata"]["name"] == "cotizacion"
    )
    containers = cotizacion["spec"]["template"]["spec"]["containers"]
    if not any(
        env["name"] == "PERFILAMIENTO_SERVICE_URL" and env.get("value") == URL
        for container in containers
        for env in container.get("env", [])
    ):
        raise RuntimeError("Cotizacion no apunta al servicio productivo.")
    for namespace, name in (("cotizacion", "cotizacion"), ("bff-web", "bff-web")):
        kubectl("-n", namespace, "rollout", "status", f"deployment/{name}", "--timeout=300s")
    controllers = resource("get", "deployments,statefulsets,daemonsets,jobs,cronjobs", "-A")
    for item in controllers["items"]:
        meta = item["metadata"]
        if meta["namespace"] == LEGACY and (
            item["kind"] != "Deployment" or meta["name"] not in WORKLOADS
        ):
            raise RuntimeError("Controlador HU11 adicional: revisar dependencias antes de retirar.")
        if meta["namespace"] != LEGACY and LEGACY in json.dumps(item["spec"]):
            raise RuntimeError("Un workload externo todavia referencia HU11.")
    autoscalers = resource("-n", LEGACY, "get", "hpa")
    if autoscalers["items"]:
        raise RuntimeError("HU11 conserva autoscalers: revisar antes de retirar.")
    kubectl("-n", LEGACY, "delete", "deployment", *WORKLOADS, "--ignore-not-found", "--wait=true")
    if resource("-n", LEGACY, "get", "deployments")["items"]:
        raise RuntimeError("HU11 todavia tiene deployments.")
    kubectl("-n", LEGACY, "wait", "--for=delete", "pods", "--all", "--timeout=180s")
    print("Retirados solo los cuatro deployments HU11; namespace, servicios y datos conservados.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("stage", "retire"))
    parser.add_argument("--acceptance-confirmed", action="store_true")
    args = parser.parse_args()
    if args.action == "stage":
        stage()
    else:
        retire(acceptance_confirmed=args.acceptance_confirmed)


if __name__ == "__main__":
    main()
