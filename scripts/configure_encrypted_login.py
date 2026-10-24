"""Operación explícita de dos Deployments; nunca crea secretos ni ejecuta Terraform."""

import argparse
import json
import subprocess
from typing import Any

IDENTITY_CHECK = """
from datetime import UTC, datetime
import json
from jwcrypto import jwe, jwk
from redis import Redis
from identidad.config import Config
from identidad.infrastructure.encrypted_login import EncryptedLogin, PREFIX
c = Config.from_environment()
r = Redis.from_url(c.redis_url, decode_responses=True, socket_timeout=2, socket_connect_timeout=2)
s = EncryptedLogin(c.login_encryption_private_key, c.jwt_private_key, r, lambda: datetime.now(UTC))
issued = s.challenge()
try:
    assert 58 <= r.ttl(PREFIX + issued["desafioId"]) <= 60
    public = issued["clavePublica"]
    header = dict(alg="RSA-OAEP-256", enc="A256GCM", kid=public["kid"], typ="solventa-login+jwe")
    body = dict(
        desafioId=issued["desafioId"], correo="preflight@example.com", contrasena="sintetica"
    )
    envelope = jwe.JWE(json.dumps(body).encode(), protected=json.dumps(header))
    envelope.add_recipient(jwk.JWK(**public))
    assert s.decrypt_and_consume(body["correo"], envelope.serialize(compact=True)) == "sintetica"
    assert not r.exists(PREFIX + issued["desafioId"])
finally:
    r.delete(PREFIX + issued["desafioId"])
    r.close()
print("Clave independiente, JWE y Redis GETDEL verificados; no se autenticaron usuarios.")
"""

BFF_CHECK = """
import json
import os
from urllib.request import urlopen
with urlopen("http://127.0.0.1:8080/v1/sesiones/desafio", timeout=5) as response:
    assert response.status == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert set(json.load(response)) == {"desafioId", "clavePublica", "expiraEn"}
print("BFF obtiene desafio de identidad; falta comprobar gateway y login web por separado.")
"""


def kubectl(context: str, service: str, *arguments: str) -> str:
    result = subprocess.run(
        ["kubectl", "--context", context, "-n", service, *arguments],
        capture_output=True,
        text=True,
        timeout=330,
        check=True,
    )
    return result.stdout


def configure(args: argparse.Namespace) -> None:
    def run(*arguments: str) -> str:
        return kubectl(args.context, args.service, *arguments)

    deployment: dict[str, Any] = json.loads(run("get", "deployment", args.service, "-o", "json"))
    assert deployment["spec"]["replicas"] == 1, "Se requiere el perfil de una réplica"
    containers = deployment["spec"]["template"]["spec"]["containers"]
    container = next(item for item in containers if item["name"] == args.service)
    expected = args.expected_image if args.action == "prepare" else args.image
    assert expected and container["image"] == expected, "La imagen actual no coincide"
    revision = deployment["metadata"]["annotations"]["deployment.kubernetes.io/revision"]
    print(f"{args.service}: revisión previa {revision}; imagen {container['image']}", flush=True)

    def verify(upstream: bool = False) -> None:
        code = IDENTITY_CHECK if args.service == "identidad" else BFF_CHECK
        if upstream and args.service == "bff-web":
            code = code.replace(
                '"http://127.0.0.1:8080/v1/sesiones/desafio"',
                'os.environ["IDENTIDAD_URL"].rstrip("/") + "/oauth/login-challenge"',
            )
        print(
            run(
                "exec", f"deployment/{args.service}", "-c", args.service, "--", "python", "-c", code
            )
        )

    if args.action == "verify":
        verify()
        return
    if args.action == "enable":
        # Identidad debe estar lista antes de habilitar el BFF; /ping no acredita esta frontera.
        verify(upstream=True)
    environment: list[dict[str, Any]] = [
        {"name": "ENCRYPTED_LOGIN_ENABLED", "value": str(args.action == "enable").lower()}
    ]
    if args.action == "prepare" and args.service == "identidad":
        assert args.secret_name, "--secret-name es obligatorio para preparar identidad"
        present = run(
            "get",
            "secret",
            args.secret_name,
            "-o",
            'go-template={{if index .data "LOGIN_ENCRYPTION_PRIVATE_KEY"}}presente{{end}}',
        )
        assert present == "presente", "Falta LOGIN_ENCRYPTION_PRIVATE_KEY en el secreto dedicado"
        environment.append(
            {
                "name": "LOGIN_ENCRYPTION_PRIVATE_KEY",
                "valueFrom": {
                    "secretKeyRef": {
                        "name": args.secret_name,
                        "key": "LOGIN_ENCRYPTION_PRIVATE_KEY",
                        "optional": False,
                    }
                },
            }
        )
    patch = {
        "metadata": {"resourceVersion": deployment["metadata"]["resourceVersion"]},
        "spec": {
            "template": {
                "spec": {
                    "containers": [{"name": args.service, "image": args.image, "env": environment}]
                }
            }
        },
    }
    run("patch", "deployment", args.service, "--type=strategic", "-p", json.dumps(patch))
    run("rollout", "status", f"deployment/{args.service}", "--timeout=300s")
    if args.action == "enable" or (args.action == "prepare" and args.service == "identidad"):
        verify()
    print("Configuración completada. No se modificaron otros recursos ni el gateway.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "enable", "disable", "verify"))
    parser.add_argument("--context", required=True)
    parser.add_argument("--service", required=True, choices=("identidad", "bff-web"))
    parser.add_argument("--image", required=True)
    parser.add_argument("--expected-image")
    parser.add_argument("--secret-name")
    args = parser.parse_args()
    try:
        configure(args)
    except (AssertionError, KeyError, StopIteration, subprocess.SubprocessError) as error:
        # No imprimir salida del pod ni contenidos de Secret en un fallo.
        raise SystemExit(
            f"Operación incompleta ({type(error).__name__}). "
            "Detener activación web y seguir rollback coordinado del runbook."
        ) from None


if __name__ == "__main__":
    main()
