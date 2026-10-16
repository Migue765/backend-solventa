import argparse
import base64
import json
import secrets
import subprocess
from pathlib import Path

from bff_web.infrastructure.passwords import hash_password
from sqlalchemy.engine import make_url

NAMESPACE = "hu11-perfilamiento"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Despliegue aislado de HU11 en el cluster existente."
    )
    parser.add_argument("--image", required=True)
    parser.add_argument("--kubeconfig", required=True)
    parser.add_argument("--access-file", type=Path, required=True)
    args = parser.parse_args()
    kubectl = ["kubectl", "--kubeconfig", args.kubeconfig]

    def apply(resource: dict[str, object]) -> None:
        subprocess.run(
            [*kubectl, "apply", "-f", "-"],
            input=json.dumps(resource),
            text=True,
            check=True,
        )

    source = json.loads(
        subprocess.check_output(
            [*kubectl, "-n", "cotizacion", "get", "secret", "cotizacion-db", "-o", "json"]
        )
    )
    url = make_url(base64.b64decode(source["data"]["DATABASE_URL"]).decode()).set(
        drivername="postgresql+psycopg"
    )
    access = json.loads(args.access_file.read_text())
    apply({"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": NAMESPACE}})
    previous_raw = subprocess.check_output(
        [
            *kubectl,
            "-n",
            NAMESPACE,
            "get",
            "secret",
            "hu11-runtime",
            "-o",
            "json",
            "--ignore-not-found",
        ]
    )
    previous = json.loads(previous_raw) if previous_raw.strip() else None
    token = (
        base64.b64decode(previous["data"]["INTERNAL_API_TOKEN"]).decode()
        if previous
        else secrets.token_urlsafe(32)
    )
    credentials = [
        {
            "id": "f2222222-2222-4222-8222-222222222222",
            "nombre": "Cliente HU11",
            "correo": access["email"],
            "rol": "cliente",
            "tipoDocumento": "cc",
            "identificacion": "1234567890",
            "telefono": "+573001234567",
            "passwordHash": hash_password(access["password"]),
        }
    ]
    secret = {
        "DATABASE_URL": url.render_as_string(hide_password=False),
        "INTERNAL_API_TOKEN": token,
        "AUTH_DEVELOPMENT_USERS_JSON": json.dumps(credentials),
    }
    for service in ("identidad", "perfilamiento", "cotizacion"):
        secret[f"{service.upper()}_DATABASE_URL"] = url.update_query_dict(
            {"options": f"-csearch_path=hu11_{service}"}
        ).render_as_string(hide_password=False)
    apply(
        {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {"name": "hu11-runtime", "namespace": NAMESPACE},
            "type": "Opaque",
            "stringData": secret,
        }
    )

    def secret_env(name: str, key: str | None = None) -> dict[str, object]:
        return {
            "name": name,
            "valueFrom": {"secretKeyRef": {"name": "hu11-runtime", "key": key or name}},
        }

    def value_env(name: str, value: str) -> dict[str, object]:
        return {"name": name, "value": value}

    init_name = "hu11-init-" + args.image.rsplit(":", 1)[-1].replace(".", "-")[-35:]
    apply(
        {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {"name": init_name, "namespace": NAMESPACE},
            "spec": {
                "backoffLimit": 0,
                "ttlSecondsAfterFinished": 3600,
                "template": {
                    "spec": {
                        "restartPolicy": "Never",
                        "containers": [
                            {
                                "name": "init",
                                "image": args.image,
                                "command": ["python", "/app/init_database.py"],
                                "env": [
                                    secret_env("DATABASE_URL"),
                                    secret_env(
                                        "HU11_COTIZACION_DATABASE_URL", "COTIZACION_DATABASE_URL"
                                    ),
                                ],
                            }
                        ],
                    }
                },
            },
        }
    )
    subprocess.run(
        [
            *kubectl,
            "-n",
            NAMESPACE,
            "wait",
            "--for=condition=complete",
            f"job/{init_name}",
            "--timeout=300s",
        ],
        check=True,
    )
    for service in ("identidad", "perfilamiento", "cotizacion", "bff-web"):
        module = service.replace("-", "_")
        env = [
            secret_env("INTERNAL_API_TOKEN"),
            value_env("SERVICE_APP", f"{module}.app:create_app()"),
        ]
        if service in {"identidad", "perfilamiento"}:
            env.append(secret_env(f"{service.upper()}_DATABASE_URL"))
        elif service == "cotizacion":
            env.extend(
                [
                    secret_env("DATABASE_URL", "COTIZACION_DATABASE_URL"),
                    value_env("PERFILAMIENTO_SERVICE_URL", "http://perfilamiento:8080"),
                    value_env("COTIZACION_PERFILAMIENTO_HABILITADO", "true"),
                    value_env("DB_POOL_SIZE", "2"),
                    value_env("DB_MAX_OVERFLOW", "2"),
                ]
            )
        else:
            env.extend(
                [
                    secret_env("AUTH_DEVELOPMENT_USERS_JSON"),
                    value_env("QUOTE_SERVICE_URL", "http://cotizacion:8080"),
                    value_env("SESSION_COOKIE_SECURE", "true"),
                    value_env("SESSION_COOKIE_NAME", "solventa_hu11_session"),
                    value_env("SESSION_COOKIE_SAMESITE", "Lax"),
                    value_env(
                        "CORS_ALLOWED_ORIGINS", "https://localhost:4200,http://localhost:4200"
                    ),
                ]
            )
        if service in {"bff-web", "perfilamiento"}:
            env.append(value_env("IDENTIDAD_SERVICE_URL", "http://identidad:8080"))
        labels = {"app": f"hu11-{service}"}
        probe = {
            "httpGet": {
                "path": "/ping" if service in {"bff-web", "cotizacion"} else "/health",
                "port": 8080,
            },
            "initialDelaySeconds": 5,
            "periodSeconds": 10,
        }
        apply(
            {
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "metadata": {"name": service, "namespace": NAMESPACE},
                "spec": {
                    "replicas": 1,
                    "selector": {"matchLabels": labels},
                    "template": {
                        "metadata": {"labels": labels},
                        "spec": {
                            "automountServiceAccountToken": False,
                            "securityContext": {"runAsNonRoot": True, "runAsUser": 10001},
                            "containers": [
                                {
                                    "name": service,
                                    "image": args.image,
                                    "ports": [{"containerPort": 8080}],
                                    "env": env,
                                    "resources": {
                                        "requests": {"cpu": "50m", "memory": "64Mi"},
                                        "limits": {"cpu": "300m", "memory": "256Mi"},
                                    },
                                    "readinessProbe": probe,
                                    "livenessProbe": {**probe, "initialDelaySeconds": 20},
                                    "securityContext": {
                                        "allowPrivilegeEscalation": False,
                                        "capabilities": {"drop": ["ALL"]},
                                    },
                                }
                            ],
                        },
                    },
                },
            }
        )
        apply(
            {
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {"name": service, "namespace": NAMESPACE},
                "spec": {"selector": labels, "ports": [{"port": 8080, "targetPort": 8080}]},
            }
        )
    for service in ("identidad", "perfilamiento", "cotizacion", "bff-web"):
        subprocess.run(
            [
                *kubectl,
                "-n",
                NAMESPACE,
                "rollout",
                "status",
                f"deployment/{service}",
                "--timeout=240s",
            ],
            check=True,
        )
    print("Servicios HU11 listos; el enrutamiento publico se configura por separado.")


if __name__ == "__main__":
    main()
