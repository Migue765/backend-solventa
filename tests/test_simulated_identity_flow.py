from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Thread
from typing import Any
from uuid import UUID, uuid4

import jwt
import pytest
import yaml
from alembic import command
from alembic.config import Config as AlembicConfig
from bff_movil.app import create_app as create_mobile
from bff_movil.config import Config as MobileConfig
from bff_movil.infrastructure.http_identity_gateway import HttpIdentityGateway
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from flask import Flask
from flask.testing import FlaskClient
from identidad.app import create_app as create_identity
from identidad.config import Config
from identidad.infrastructure.persistence.models import UsuarioModel
from openapi_schema_validator import OAS31Validator
from sqlalchemy import create_engine, update
from sqlalchemy.orm import Session
from werkzeug.serving import make_server
from werkzeug.test import TestResponse

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "/movil/v1/verificaciones-identidad"
INTERNAL_PREFIX = "/v1/verificaciones-identidad"
PASSWORD = "Demostracion123!"
ISSUER = "https://identidad.example.test"
CORRELATION = str(uuid4())


@pytest.fixture(scope="module")
def verification_key() -> str:
    return (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        .decode()
    )


def checked(response: TestResponse, status: int = 200) -> dict[str, Any]:
    assert response.status_code == status, response.get_data(as_text=True)
    assert response.headers["Cache-Control"] == "no-store"
    assert UUID(response.headers["X-Correlation-ID"])
    data: dict[str, Any] = response.get_json()
    document = yaml.safe_load(
        (ROOT / "contracts" / "openapi" / "identidad-simulada-v1.yaml").read_text(encoding="utf-8")
    )
    schema = document["components"]["schemas"]["Verificacion" if status == 200 else "Problema"]
    OAS31Validator(schema, format_checker=OAS31Validator.FORMAT_CHECKER).validate(data)
    if status >= 400:
        assert response.content_type == "application/problem+json"
        assert data["status"] == status
    return data


@dataclass
class Runtime:
    identity: Flask
    mobile: FlaskClient
    config: Config
    headers: dict[str, str]
    refresh: str
    user_id: UUID
    url: str

    def start(self) -> dict[str, Any]:
        return checked(self.mobile.post(PREFIX, json={}, headers=self.headers))

    def submit(self, verification_id: str, payload: dict[str, str]) -> TestResponse:
        return self.mobile.post(
            f"{PREFIX}/{verification_id}/intentos", json=payload, headers=self.headers
        )


def session_for(app: Flask, email: str) -> dict[str, Any]:
    client = app.test_client()
    created = client.post(
        "/v1/usuarios",
        json={"nombre": "Cuenta Sintetica", "correo": email, "contrasena": PASSWORD},
    )
    assert created.status_code == 201
    response = client.post(
        "/oauth/token",
        json={
            "grant_type": "password",
            "client_id": "movil",
            "username": email,
            "password": PASSWORD,
        },
    )
    assert response.status_code == 200
    result: dict[str, Any] = response.get_json()
    return result


@pytest.fixture
def runtime(
    tmp_path: Path,
    verification_key: str,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> Iterator[Runtime]:
    database_url = f"sqlite:///{tmp_path / 'identidad.db'}"
    monkeypatch.delenv("DB_SCHEMA", raising=False)
    migrations = AlembicConfig(str(ROOT / "services" / "identidad" / "alembic.ini"))
    migrations.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(migrations, "head")
    config = Config(
        database_url=database_url,
        jwt_private_key=verification_key,
        jwt_issuer=ISSUER,
        password_iterations=1000,
        simulated_identity_enabled=True,
        simulated_identity_outcomes=getattr(request, "param", ("aprobado",)),
    )
    identity = create_identity(config)
    issued = session_for(identity, "sintetica@example.com")
    server = make_server("127.0.0.1", 0, identity, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    mobile = create_mobile(
        MobileConfig(identity_url=url, jwt_issuer=ISSUER, simulated_identity_enabled=True)
    ).test_client()
    try:
        # Readiness is checked through real JWKS HTTP on the first protected BFF request.
        yield Runtime(
            identity,
            mobile,
            config,
            {"Authorization": f"Bearer {issued['access_token']}", "X-Correlation-ID": CORRELATION},
            issued["refresh_token"],
            UUID(issued["usuario"]["id"]),
            url,
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def attempt() -> dict[str, str]:
    return {
        "intentoId": str(uuid4()),
        "documentoRef": "fixture:documento-v1",
        "selfieRef": "fixture:selfie-v1",
    }


@pytest.mark.parametrize(
    "runtime",
    [("documento_no_valido", "aprobado"), ("prueba_vida_no_valida", "aprobado")],
    indirect=True,
)
def test_http_rejection_retry_replay_and_durable_approval(runtime: Runtime) -> None:
    initial = runtime.start()
    verification_id = initial["verificacionId"]
    assert initial == {
        "verificacionId": verification_id,
        "estado": "pendiente",
        "intentos": 0,
        "maxIntentos": 3,
        "reintentoPermitido": False,
        "simulada": True,
        "motivo": None,
    }
    assert runtime.start() == initial
    first = attempt()
    rejected = checked(runtime.submit(verification_id, first))
    assert rejected["estado"] == "rechazado" and rejected["intentos"] == 1
    assert rejected["motivo"] == runtime.config.simulated_identity_outcomes[0]
    assert rejected["reintentoPermitido"] is True
    assert checked(runtime.submit(verification_id, first)) == rejected
    mutated = {**first, "selfieRef": "fixture:selfie-v2"}
    assert (
        checked(runtime.submit(verification_id, mutated), 409)["code"] == "conflicto_idempotencia"
    )
    second = attempt()
    approved = checked(runtime.submit(verification_id, second))
    assert approved["estado"] == "aprobado" and approved["intentos"] == 2
    assert approved["motivo"] is None and approved["reintentoPermitido"] is False
    assert checked(runtime.submit(verification_id, first)) == rejected
    assert checked(runtime.submit(verification_id, second)) == approved
    assert checked(runtime.submit(verification_id, attempt()), 409)["code"] == "transicion_invalida"
    current = runtime.mobile.get(f"{PREFIX}/{verification_id}", headers=runtime.headers)
    assert checked(current) == approved == runtime.start()
    assert current.headers["X-Correlation-ID"] == CORRELATION
    restarted = create_identity(replace(runtime.config, simulated_identity_outcomes=("aprobado",)))
    assert (
        checked(
            restarted.test_client().get(
                f"{INTERNAL_PREFIX}/{verification_id}", headers=runtime.headers
            )
        )
        == approved
    )


@pytest.mark.parametrize("runtime", [("prueba_vida_no_valida",)], indirect=True)
def test_exhausted_session_does_not_reset_and_configuration_is_frozen(runtime: Runtime) -> None:
    verification_id = runtime.start()["verificacionId"]
    changed = create_identity(replace(runtime.config, simulated_identity_outcomes=("aprobado",)))
    payloads = [attempt() for _ in range(3)]
    for count, payload in enumerate(payloads, 1):
        response = changed.test_client().post(
            f"{INTERNAL_PREFIX}/{verification_id}/intentos", json=payload, headers=runtime.headers
        )
        state = checked(response)
        assert state["estado"] == "rechazado" and state["intentos"] == count
        assert state["reintentoPermitido"] is (count < 3)
    assert runtime.start() == state
    assert checked(runtime.submit(verification_id, payloads[0]))["intentos"] == 1
    assert checked(runtime.submit(verification_id, attempt()), 409)["code"] == "transicion_invalida"


def test_ownership_revocation_and_default_disabled(runtime: Runtime) -> None:
    verification_id = runtime.start()["verificacionId"]
    other = session_for(runtime.identity, "otra-sintetica@example.com")
    headers = {"Authorization": f"Bearer {other['access_token']}"}
    for target in (verification_id, str(uuid4())):
        assert (
            checked(runtime.mobile.get(f"{PREFIX}/{target}", headers=headers), 404)["code"]
            == "verificacion_no_encontrada"
        )
        assert (
            checked(
                runtime.mobile.post(f"{PREFIX}/{target}/intentos", headers=headers, json=attempt()),
                404,
            )["code"]
            == "verificacion_no_encontrada"
        )
    assert checked(runtime.mobile.post(PREFIX, json={}), 401)["code"] == "sesion_invalida"
    disabled_identity = create_identity(replace(runtime.config, simulated_identity_enabled=False))
    assert (
        checked(
            disabled_identity.test_client().post(INTERNAL_PREFIX, json={}, headers=runtime.headers),
            503,
        )["code"]
        == "servicio_no_disponible"
    )
    disabled_mobile = create_mobile(
        MobileConfig(identity_url=runtime.url, jwt_issuer=ISSUER)
    ).test_client()
    assert (
        checked(disabled_mobile.post(PREFIX, json={}, headers=runtime.headers), 503)["code"]
        == "servicio_no_disponible"
    )
    assert (
        runtime.identity.test_client()
        .post("/oauth/revoke", json={"refresh_token": runtime.refresh})
        .status_code
        == 204
    )
    for client, prefix in (
        (runtime.mobile, PREFIX),
        (runtime.identity.test_client(), INTERNAL_PREFIX),
    ):
        assert (
            checked(client.get(f"{prefix}/{verification_id}", headers=runtime.headers), 401)["code"]
            == "sesion_invalida"
        )
        assert (
            checked(client.post(prefix, json={}, headers=runtime.headers), 401)["code"]
            == "sesion_invalida"
        )


@pytest.mark.parametrize(
    "mutation",
    [
        {"client_id": "web"},
        {"exp": 1},
        {"aud": "otra-api"},
        {"sid": str(uuid4())},
        {"sub": str(uuid4())},
        {"scope": "asesor"},
    ],
)
def test_invalid_or_ineligible_tokens(runtime: Runtime, mutation: dict[str, object]) -> None:
    claims = jwt.decode(runtime.headers["Authorization"][7:], options={"verify_signature": False})
    claims.update(mutation)
    issuer = runtime.identity.extensions["jwt_issuer"]
    token = jwt.encode(
        claims, runtime.config.jwt_private_key, algorithm="RS256", headers={"kid": issuer.kid}
    )
    status = 403 if "scope" in mutation else 401
    for client, prefix in (
        (runtime.mobile, PREFIX),
        (runtime.identity.test_client(), INTERNAL_PREFIX),
    ):
        checked(client.post(prefix, json={}, headers={"Authorization": f"Bearer {token}"}), status)


def test_current_role_is_authoritative(runtime: Runtime) -> None:
    engine = create_engine(runtime.config.database_url)
    with Session(engine) as session:
        session.execute(
            update(UsuarioModel)
            .where(UsuarioModel.usuario_id == runtime.user_id)
            .values(rol="asesor")
        )
        session.commit()
    for client, prefix in (
        (runtime.mobile, PREFIX),
        (runtime.identity.test_client(), INTERNAL_PREFIX),
    ):
        assert (
            checked(client.post(prefix, json={}, headers=runtime.headers), 403)["code"]
            == "acceso_denegado"
        )
    engine.dispose()


@pytest.mark.parametrize(
    "extra",
    [
        {"aprobado": True},
        {"estado": "aprobado"},
        {"clienteId": str(uuid4())},
        {"documentoRef": "https://example.com/documento"},
        {"selfieRef": "data:image/png;base64,abc"},
        {"selfie": "no-debe-guardarse"},
        {"intentoId": True},
    ],
)
def test_rejects_results_images_pii_and_unknown_fields(
    runtime: Runtime, extra: dict[str, object]
) -> None:
    verification_id = runtime.start()["verificacionId"]
    payload = {**attempt(), **extra}
    for client, prefix in (
        (runtime.mobile, PREFIX),
        (runtime.identity.test_client(), INTERNAL_PREFIX),
    ):
        response = client.post(
            f"{prefix}/{verification_id}/intentos", json=payload, headers=runtime.headers
        )
        assert checked(response, 400)["code"] == "solicitud_invalida"
        assert "no-debe-guardarse" not in response.get_data(as_text=True)
    assert runtime.start()["intentos"] == 0


def test_rejects_query_malformed_oversized_and_get_body(runtime: Runtime) -> None:
    verification_id = runtime.start()["verificacionId"]
    for client, prefix in (
        (runtime.mobile, PREFIX),
        (runtime.identity.test_client(), INTERNAL_PREFIX),
    ):
        for response in (
            client.post(prefix + "?resultado=aprobado", json={}, headers=runtime.headers),
            client.post(prefix, data="{", content_type="application/json", headers=runtime.headers),
            client.post(
                prefix, data="x" * 2049, content_type="application/json", headers=runtime.headers
            ),
            client.get(f"{prefix}/{verification_id}", json={}, headers=runtime.headers),
        ):
            checked(response, 400)
    assert runtime.start()["intentos"] == 0


@pytest.mark.parametrize("runtime", [("documento_no_valido",)], indirect=True)
def test_concurrent_start_and_attempts_are_serialized(runtime: Runtime) -> None:
    def start(_: int) -> dict[str, Any]:
        return checked(
            runtime.identity.test_client().post(INTERNAL_PREFIX, json={}, headers=runtime.headers)
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        started = list(pool.map(start, range(4)))
    assert all(value == started[0] for value in started)
    verification_id = started[0]["verificacionId"]
    path = f"{INTERNAL_PREFIX}/{verification_id}/intentos"
    duplicate = attempt()

    def submit(payload: dict[str, str]) -> TestResponse:
        return runtime.identity.test_client().post(path, json=payload, headers=runtime.headers)

    with ThreadPoolExecutor(max_workers=4) as pool:
        duplicates = list(pool.map(submit, [duplicate] * 4))
    assert all(checked(response)["intentos"] == 1 for response in duplicates)
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(submit, [attempt() for _ in range(4)]))
    assert sorted(response.status_code for response in responses) == [200, 200, 409, 409]
    assert runtime.start()["intentos"] == 3


def test_upstream_failure_never_falls_back_to_approval(runtime: Runtime) -> None:
    class BrokenGateway(HttpIdentityGateway):
        def verification_request(
            self,
            method: str,
            path: str,
            body: dict[str, object] | None,
            bearer: str,
            correlation_id: str,
        ) -> dict[str, Any]:
            return {
                "verificacionId": str(uuid4()),
                "estado": "aprobado",
                "intentos": 0,
                "maxIntentos": 3,
                "reintentoPermitido": False,
                "simulada": True,
                "motivo": None,
            }

    mobile = create_mobile(
        MobileConfig(identity_url=runtime.url, jwt_issuer=ISSUER, simulated_identity_enabled=True),
        verification_gateway=BrokenGateway(runtime.url),
    ).test_client()
    assert (
        checked(mobile.post(PREFIX, json={}, headers=runtime.headers), 503)["code"]
        == "servicio_no_disponible"
    )
