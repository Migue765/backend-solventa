"""HU21 (SOL-309): BFF movil -> HTTP -> Siniestros con base migrada."""

import json
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Thread
from time import perf_counter
from typing import Any
from uuid import uuid4

import jwt
import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from bff_movil.app import create_app
from bff_movil.config import Config
from bff_movil.infrastructure.jwt_verifier import JwksKeyStore, JwtVerifier
from cryptography.hazmat.primitives.asymmetric import rsa
from flask import Flask
from flask.testing import FlaskClient
from jwt.algorithms import RSAAlgorithm
from siniestros.app import create_app as create_claims_app
from siniestros.domain import Config as ClaimsConfig
from werkzeug.serving import make_server

from tests.test_claims_flow import register, validate_contract
from tests.test_dashboard_flow import INTERNAL_TOKEN, ISSUER, ROOT, USER, bearer

KID = "claims-mobile-test"
Runtime = tuple[FlaskClient, Flask]


@pytest.fixture(scope="module")
def keypair() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def sign_mobile(key: rsa.RSAPrivateKey, subject: str = str(USER.user_id)) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": subject,
            "iss": ISSUER,
            "aud": "solventa-api",
            "iat": now,
            "exp": now + timedelta(hours=1),
            "jti": str(uuid4()),
            "client_id": "movil",
            "scope": "cliente",
            "nombre": USER.name,
            "correo": USER.email,
            "rol": "cliente",
        },
        key,
        algorithm="RS256",
        headers={"kid": KID},
    )


@pytest.fixture
def token(keypair: rsa.RSAPrivateKey) -> str:
    return sign_mobile(keypair)


@contextmanager
def serve(app: Callable[..., Any]) -> Iterator[str]:
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def mobile_bff(keypair: rsa.RSAPrivateKey, claims_url: str) -> Flask:
    jwk = json.loads(RSAAlgorithm.to_jwk(keypair.public_key()))
    jwk.update(kid=KID, use="sig", alg="RS256")
    app: Flask = create_app(
        Config(
            identity_url="http://identidad.test",
            jwt_issuer=ISSUER,
            claims_service_url=claims_url,
            internal_api_token=INTERNAL_TOKEN,
        ),
        token_verifier=JwtVerifier(
            JwksKeyStore(lambda: {"keys": [jwk]}), ISSUER, "solventa-api", "movil"
        ),
    )
    return app


@pytest.fixture
def runtime(tmp_path: Path, keypair: rsa.RSAPrivateKey) -> Iterator[Runtime]:
    url = f"sqlite:///{tmp_path / 'siniestros.db'}"
    migration_config = AlembicConfig(str(ROOT / "services" / "siniestros" / "alembic.ini"))
    migration_config.set_main_option("sqlalchemy.url", url)
    command.upgrade(migration_config, "head")
    claims_app = create_claims_app(
        ClaimsConfig(url, tmp_path / "evidencias", INTERNAL_TOKEN, "http://polizas.test")
    )
    try:
        with serve(claims_app) as claims_url:
            yield mobile_bff(keypair, claims_url).test_client(), claims_app
    finally:
        claims_app.extensions["siniestros_engine"].dispose()


def path(claim_id: object) -> str:
    return f"/movil/v1/siniestros/{claim_id}/estado"


def test_movil_consulta_estado_e_historial_compartido(runtime: Runtime, token: str) -> None:
    client, claims_app = runtime
    claim_id = register(claims_app, USER.user_id)

    response = client.get(path(claim_id), headers=bearer(token))

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    body = response.get_json()
    validate_contract(body, "EstadoSiniestro")
    assert body["id"] == str(claim_id)
    assert body["estado"] == "en_inspeccion"
    assert body["actualizadoEn"] == "2026-11-01T18:00:00Z"
    assert [entry["estado"] for entry in body["historial"]] == [
        "reportado",
        "en_evaluacion",
        "en_inspeccion",
    ]


def test_movil_no_revela_siniestros_de_otro_cliente(
    runtime: Runtime, keypair: rsa.RSAPrivateKey
) -> None:
    client, claims_app = runtime
    claim_id = register(claims_app, USER.user_id)
    other = bearer(sign_mobile(keypair, str(uuid4())))

    foreign = client.get(path(claim_id), headers=other)
    missing = client.get(path(uuid4()), headers=other)

    assert foreign.status_code == missing.status_code == 404
    foreign_body, missing_body = foreign.get_json(), missing.get_json()
    foreign_body.pop("correlationId")
    missing_body.pop("correlationId")
    assert foreign_body == missing_body
    assert foreign_body["code"] == "siniestro_no_encontrado"
    assert str(claim_id) not in foreign.get_data(as_text=True)


def test_demora_de_siniestros_es_falla_acotada_y_no_estado(
    keypair: rsa.RSAPrivateKey, token: str
) -> None:
    def slow(environ: dict[str, Any], start_response: Callable[..., Any]) -> list[bytes]:
        time.sleep(5)
        start_response("200 OK", [("Content-Type", "application/json")])
        return [b'{"estado": "reportado", "historial": []}']

    with serve(slow) as claims_url:
        client = mobile_bff(keypair, claims_url).test_client()
        start = perf_counter()
        response = client.get(path(uuid4()), headers=bearer(token))
        elapsed = perf_counter() - start

    assert response.status_code == 503
    assert response.get_json()["code"] == "servicio_no_disponible"
    assert "estado" not in response.get_json()
    assert elapsed < 3


def test_latencia_extremo_a_extremo_movil(runtime: Runtime, token: str) -> None:
    client, claims_app = runtime
    claims = [register(claims_app, USER.user_id) for _ in range(20)]
    headers = bearer(token)
    for claim_id in claims:
        assert client.get(path(claim_id), headers=headers).status_code == 200
    latencies_ms = []
    for index in range(200):
        start = perf_counter()
        response = client.get(path(claims[index % len(claims)]), headers=headers)
        latencies_ms.append((perf_counter() - start) * 1000)
        assert response.status_code == 200
    ordered = sorted(latencies_ms)

    assert ordered[189] <= 150, f"p95={ordered[189]:.1f}ms"
    assert ordered[197] <= 300, f"p99={ordered[197]:.1f}ms"
