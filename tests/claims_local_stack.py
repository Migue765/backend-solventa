"""Owned loopback stack with real Identity, Policies, Claims, SQL and private files."""

import argparse
import json
import os
import secrets
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event, Thread
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from alembic import command
from alembic.config import Config as MigrationConfig
from bff_movil.app import create_app as mobile_app
from bff_movil.config import Config as MobileConfig
from bff_web.app import create_app as web_app
from bff_web.config import Config as WebConfig
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from flask import Flask
from identidad.app import create_app as identity_app
from identidad.config import Config as IdentityConfig
from polizas.app import create_app as policies_app
from siniestros.app import create_app as claims_app
from siniestros.domain import Config as ClaimsConfig
from werkzeug.serving import WSGIRequestHandler, make_server

ROOT = Path(__file__).resolve().parents[1]


class QuietHandler(WSGIRequestHandler):
    def log(self, type: str, message: str, *args: Any) -> None:
        pass


def call(
    url: str,
    method: str = "GET",
    data: Any = None,
    token: str | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, Any]:
    outgoing = dict(headers or {})
    if token:
        outgoing["Authorization"] = f"Bearer {token}"
    if data is not None and not isinstance(data, bytes):
        data = json.dumps(data).encode()
        outgoing["Content-Type"] = "application/json"
    request = Request(url, method=method, data=data, headers=outgoing)
    try:
        response = urlopen(request, timeout=180)
    except HTTPError as error:
        response = error
    with response:
        body = response.read()
        if response.headers.get_content_type() in {"application/json", "application/problem+json"}:
            return response.status, json.loads(body)
        return response.status, body


@contextmanager
def serve(app: Flask) -> Iterator[str]:
    server = make_server("127.0.0.1", 0, app, threaded=True, request_handler=QuietHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def migrate(service: str, database_url: str) -> None:
    config = MigrationConfig(str(ROOT / "services" / service / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")


@contextmanager
def stack(directory: Path, **limits: Any) -> Iterator[dict[str, Any]]:
    __tracebackhide__ = True
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    internal = secrets.token_urlsafe(32)
    issuer = "urn:solventa:claims-local"
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    databases = {
        service: f"sqlite:///{directory / (service + '.db')}"
        for service in ("identidad", "polizas", "siniestros")
    }
    for service, url in databases.items():
        migrate(service, url)
    identity = identity_app(
        IdentityConfig(
            database_url=databases["identidad"],
            jwt_private_key=pem,
            jwt_issuer=issuer,
            internal_api_token=internal,
            max_attempts=100,
        )
    )
    policies = policies_app(databases["polizas"], internal)
    with ExitStack() as running:
        identity_url = running.enter_context(serve(identity))
        policies_url = running.enter_context(serve(policies))
        config = ClaimsConfig(
            database_url=databases["siniestros"],
            storage_root=directory / "private",
            internal_token=internal,
            policies_url=policies_url,
            **limits,
        )
        claims = claims_app(config)
        claims_url = running.enter_context(serve(claims))
        mobile = mobile_app(
            MobileConfig(
                identity_url=identity_url,
                jwt_issuer=issuer,
                claims_service_url=claims_url,
                internal_api_token=internal,
            )
        )
        web = web_app(
            WebConfig(
                identity_url=identity_url,
                jwt_issuer=issuer,
                claims_service_url=claims_url,
                internal_api_token=internal,
            )
        )
        mobile_url = running.enter_context(serve(mobile)) + "/movil/v1"
        web_url = running.enter_context(serve(web)) + "/v1"
        users: list[dict[str, Any]] = []
        for index in range(2):
            password = secrets.token_urlsafe(24) + "aA1!"
            email = f"claims-{index}-{uuid4().hex}@example.com"
            status, created = call(
                mobile_url + "/usuarios",
                "POST",
                {
                    "nombre": f"Cliente sintetico {index}",
                    "correo": email,
                    "contrasena": password,
                },
            )
            assert status == 201, (status, created)
            login_body = {"correo": email, "contrasena": password}
            status, mobile_session = call(mobile_url + "/sesiones", "POST", login_body)
            assert status == 200, (status, mobile_session)
            status, web_session = call(web_url + "/sesiones", "POST", login_body)
            assert status == 200, (status, web_session)
            owner = mobile_session["usuario"]["id"]
            status, policy = call(
                policies_url + "/v1/polizas",
                "POST",
                {
                    "clienteId": owner,
                    "suscripcionId": str(uuid4()),
                    "pagoId": str(uuid4()),
                    "tipo": "automotive",
                    "confirmadaEn": datetime.now(UTC).isoformat(),
                    "simulada": True,
                    "fechasContratadas": None,
                },
                internal,
            )
            assert status == 200, (status, policy)
            token = mobile_session["accessToken"]
            payload = {
                "polizaId": policy["id"],
                "fechaEvento": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
                "tipoEvento": "Validacion local",
                "descripcion": "Descripcion sintetica del evento.",
                "lugar": "Ubicacion sintetica",
            }
            operation = str(uuid4())
            status, claim = call(
                mobile_url + "/siniestros", "POST", payload, token, {"Idempotency-Key": operation}
            )
            assert status == 201, (status, claim)
            users.append(
                {
                    "id": owner,
                    "email": email,
                    "password": password,
                    "token": token,
                    "webToken": web_session["accessToken"],
                    "claim": claim,
                    "claimOperation": operation,
                    "claimPayload": payload,
                }
            )
        try:
            yield {
                "mobile": mobile_url,
                "web": web_url,
                "claims": claims_url,
                "users": users,
                "config": config,
                "app": claims,
                "internal": internal,
                "policies": policies_url,
                "mobileApp": mobile,
                "webApp": web,
            }
        finally:
            claims.extensions["siniestros_repository"].engine.dispose()
            policies.extensions["polizas_engine"].dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    args = parser.parse_args()
    with stack(args.directory.resolve()) as running:
        state = {key: running[key] for key in ("mobile", "web", "users")}
        with os.fdopen(
            os.open(args.state, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w"
        ) as out:
            json.dump(state, out)
        print(json.dumps({"mobile": running["mobile"], "web": running["web"]}), flush=True)
        try:
            Event().wait()
        except KeyboardInterrupt:
            pass
        finally:
            args.state.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
