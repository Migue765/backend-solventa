"""HU25 (SOL-318): BFF Web -> HTTP -> Siniestros con base migrada."""

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Thread
from time import perf_counter
from uuid import UUID, uuid4

import pytest
import yaml
from alembic import command
from alembic.config import Config as AlembicConfig
from bff_web.app import create_app
from bff_web.config import Config
from bff_web.domain.auth import User
from bff_web.infrastructure.http_claim_gateway import HttpClaimGateway
from bff_web.infrastructure.jwt_verifier import JwksKeyStore, JwtVerifier
from cryptography.hazmat.primitives.asymmetric import rsa
from flask import Flask
from flask.testing import FlaskClient
from jwt.algorithms import RSAAlgorithm
from openapi_schema_validator import OAS31Validator
from siniestros.app import create_app as create_claims_app
from siniestros.domain import Config as ClaimsConfig
from siniestros.infrastructure.models import CambioEstadoModel
from siniestros.models import Claim
from siniestros.repository import fingerprint
from sqlalchemy import update
from sqlalchemy.orm import Session
from werkzeug.serving import make_server

from tests.test_dashboard_flow import INTERNAL_TOKEN, ISSUER, ROOT, USER, bearer, sign_token

REPORTE = datetime(2026, 11, 1, 15, 0, tzinfo=UTC)
HISTORIAL = [
    ("reportado", timedelta(0), True),
    ("en_evaluacion", timedelta(hours=1), True),
    ("en_revision", timedelta(hours=2), False),
    ("en_inspeccion", timedelta(hours=3), True),
]
Runtime = tuple[FlaskClient, Flask]


@pytest.fixture(scope="module")
def keypair() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def token(keypair: rsa.RSAPrivateKey) -> str:
    return sign_token(keypair, scope="cliente")


@pytest.fixture
def runtime(tmp_path: Path, keypair: rsa.RSAPrivateKey) -> Iterator[Runtime]:
    url = f"sqlite:///{tmp_path / 'siniestros.db'}"
    migration_config = AlembicConfig(str(ROOT / "services" / "siniestros" / "alembic.ini"))
    migration_config.set_main_option("sqlalchemy.url", url)
    command.upgrade(migration_config, "head")
    claims_app = create_claims_app(
        ClaimsConfig(url, tmp_path / "evidencias", INTERNAL_TOKEN, "http://polizas.test")
    )
    server = make_server("127.0.0.1", 0, claims_app, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    jwk = json.loads(RSAAlgorithm.to_jwk(keypair.public_key()))
    jwk.update(kid="dashboard-test", use="sig", alg="RS256")
    bff = create_app(
        Config(
            identity_url="http://identidad.test",
            jwt_issuer=ISSUER,
            claims_service_url=f"http://127.0.0.1:{server.server_port}",
            internal_api_token=INTERNAL_TOKEN,
        ),
        token_verifier=JwtVerifier(
            JwksKeyStore(lambda: {"keys": [jwk]}), ISSUER, "solventa-api", "web"
        ),
    )
    try:
        yield bff.test_client(), claims_app
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        claims_app.extensions["siniestros_engine"].dispose()


def register(claims_app: Flask, client_id: UUID) -> UUID:
    claim_id = uuid4()
    payload = {
        "polizaId": str(uuid4()),
        "fechaEvento": (REPORTE - timedelta(hours=2)).isoformat(),
        "tipoEvento": "Accidente",
        "descripcion": "Descripcion sintetica del evento.",
        "lugar": "Lugar de prueba",
    }
    with Session(claims_app.extensions["siniestros_engine"]) as session, session.begin():
        session.add(
            Claim(
                id=str(claim_id),
                owner=str(client_id),
                operation=str(uuid4()),
                fingerprint=fingerprint(payload),
                payload=payload,
                created=REPORTE.isoformat(),
                tipo="asistido",
                estado=HISTORIAL[-1][0],
                actualizado_en=REPORTE + HISTORIAL[-1][1],
            )
        )
        session.flush()
        session.add_all(
            CambioEstadoModel(
                siniestro_id=str(claim_id),
                secuencia=sequence,
                estado=state,
                ocurrido_en=REPORTE + offset,
                visible_cliente=visible,
            )
            for sequence, (state, offset, visible) in enumerate(HISTORIAL, start=1)
        )
    return claim_id


def validate_contract(payload: object, schema_name: str) -> None:
    document = yaml.safe_load((ROOT / "contracts" / "openapi" / "bff-web-v1.yaml").read_text())
    schema = {"$ref": f"#/components/schemas/{schema_name}", "components": document["components"]}
    OAS31Validator(schema, format_checker=OAS31Validator.FORMAT_CHECKER).validate(payload)


def path(claim_id: UUID) -> str:
    return f"/v1/clientes/me/siniestros/{claim_id}"


def test_cliente_consulta_estado_e_historial_permitido(runtime: Runtime, token: str) -> None:
    client, claims_app = runtime
    claim_id = register(claims_app, USER.user_id)

    response = client.get(path(claim_id), headers=bearer(token))

    assert response.status_code == 200
    body = response.get_json()
    validate_contract(body, "EstadoSiniestro")
    assert body["id"] == str(claim_id)
    assert body["estado"] == "en_inspeccion"
    assert body["tipo"] == "asistido"
    assert body["ocurridoEn"] == "2026-11-01T13:00:00Z"
    assert body["actualizadoEn"] == "2026-11-01T18:00:00Z"
    assert [entry["estado"] for entry in body["historial"]] == [
        "reportado",
        "en_evaluacion",
        "en_inspeccion",
    ]


def test_siniestro_de_otro_cliente_no_revela_datos(
    runtime: Runtime, keypair: rsa.RSAPrivateKey
) -> None:
    client, claims_app = runtime
    claim_id = register(claims_app, USER.user_id)
    other = sign_token(
        keypair, User(uuid4(), "Otro", "otro@solventa.co", "cliente"), scope="cliente"
    )

    foreign = client.get(path(claim_id), headers=bearer(other))
    missing = client.get(path(uuid4()), headers=bearer(other))

    assert foreign.status_code == missing.status_code == 404
    validate_contract(foreign.get_json(), "Problema")
    assert foreign.get_json()["code"] == missing.get_json()["code"] == "siniestro_no_encontrado"
    assert foreign.get_json()["detail"] == missing.get_json()["detail"]
    assert str(claim_id) not in foreign.get_data(as_text=True)


def test_registro_incoherente_es_falla_y_no_estado(runtime: Runtime, token: str) -> None:
    client, claims_app = runtime
    claim_id = register(claims_app, USER.user_id)
    with Session(claims_app.extensions["siniestros_engine"]) as session, session.begin():
        session.execute(update(Claim).values(estado="pagado"))

    response = client.get(path(claim_id), headers=bearer(token))

    assert response.status_code == 503
    validate_contract(response.get_json(), "Problema")
    assert "historial" not in response.get_json()


def test_servicio_caido_es_falla_y_no_historial_vacio(runtime: Runtime, token: str) -> None:
    client, claims_app = runtime
    claim_id = register(claims_app, USER.user_id)
    client.application.extensions["claim_gateway"] = HttpClaimGateway(
        "http://127.0.0.1:9", INTERNAL_TOKEN
    )

    response = client.get(path(claim_id), headers=bearer(token))

    assert response.status_code == 503
    assert response.get_json()["code"] == "servicio_no_disponible"


def test_latencia_extremo_a_extremo(runtime: Runtime, token: str) -> None:
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
