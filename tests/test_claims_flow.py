import hashlib
import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from threading import Thread
from uuid import UUID, uuid4

import jwt
import pytest
import yaml
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config as AlembicConfig
from alembic.migration import MigrationContext
from bff_web.app import create_app as create_bff
from bff_web.config import Config
from bff_web.domain.auth import User
from bff_web.infrastructure.jwt_verifier import JwksKeyStore, JwtVerifier
from cryptography.hazmat.primitives.asymmetric import rsa
from flask import Flask, Response, jsonify
from flask.testing import FlaskClient
from jwt.algorithms import RSAAlgorithm
from openapi_schema_validator import OAS31Validator
from PIL import Image
from polizas.app import create_app as create_policies
from polizas.infrastructure.models import PolizaModel
from siniestros.app import create_app
from siniestros.domain import Config as ClaimsConfig
from siniestros.models import Base, Claim, Upload
from sqlalchemy import MetaData, Table, func, select
from sqlalchemy.orm import Session
from werkzeug.serving import make_server

from tests.test_dashboard_flow import INTERNAL_TOKEN, ISSUER, ROOT, USER, bearer, sign_token

PATH = "/v1/siniestros"
INTERNAL_HEADERS = {"Authorization": f"Bearer {INTERNAL_TOKEN}"}


@contextmanager
def serve(app: Flask) -> Iterator[str]:
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def migrate(service: str, url: str) -> AlembicConfig:
    config = AlembicConfig(str(ROOT / "services" / service / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    return config


def client_token(keypair: rsa.RSAPrivateKey, user: User = USER) -> str:
    claims = jwt.decode(sign_token(keypair, user), options={"verify_signature": False})
    claims["scope"] = user.role
    return jwt.encode(claims, keypair, algorithm="RS256", headers={"kid": "dashboard-test"})


@dataclass
class Runtime:
    client: FlaskClient
    claims: Flask
    policies: Flask
    token: str
    other_token: str
    partner_token: str
    wrong_scope_token: str
    policy_id: str
    policy_url: str
    database_url: str
    migration: AlembicConfig

    def payload(self) -> dict[str, str]:
        return {
            "polizaId": self.policy_id,
            "fechaEvento": datetime.now(UTC).isoformat(),
            "tipoEvento": "Colision",
            "descripcion": "Colision reportada por el cliente.",
            "lugar": "Bogota",
        }

    def headers(self, key: UUID | None = None) -> dict[str, str]:
        return {**bearer(self.token), "Idempotency-Key": str(key or uuid4())}


@pytest.fixture
def runtime(tmp_path: Path) -> Iterator[Runtime]:
    policies_url = f"sqlite:///{tmp_path / 'polizas.db'}"
    migrate("polizas", policies_url)
    policies = create_policies(policies_url, INTERNAL_TOKEN)
    evidence = {
        "clienteId": str(USER.user_id),
        "suscripcionId": str(uuid4()),
        "pagoId": str(uuid4()),
        "tipo": "automotive",
        "confirmadaEn": datetime.now(UTC).isoformat(),
        "simulada": True,
        "fechasContratadas": None,
    }
    response = policies.test_client().post("/v1/polizas", json=evidence, headers=INTERNAL_HEADERS)
    assert response.status_code == 200
    policy_id = response.get_json()["id"]
    database_url = f"sqlite:///{tmp_path / 'siniestros.db'}"
    migration = migrate("siniestros", database_url)
    keypair = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(RSAAlgorithm.to_jwk(keypair.public_key()))
    jwk.update(kid="dashboard-test", use="sig", alg="RS256")
    with serve(policies) as policy_url:
        claims = create_app(
            ClaimsConfig(
                database_url=database_url,
                internal_token=INTERNAL_TOKEN,
                policies_url=policy_url,
                storage_root=tmp_path / "private",
            )
        )
        with serve(claims) as claim_url:
            bff = create_bff(
                Config(
                    identity_url="http://identity.test",
                    jwt_issuer=ISSUER,
                    claims_service_url=claim_url,
                    internal_api_token=INTERNAL_TOKEN,
                ),
                token_verifier=JwtVerifier(
                    JwksKeyStore(lambda: {"keys": [jwk]}), ISSUER, "solventa-api", "web"
                ),
            )
            yield Runtime(
                bff.test_client(),
                claims,
                policies,
                client_token(keypair),
                client_token(keypair, replace(USER, user_id=uuid4())),
                client_token(keypair, replace(USER, role="socio")),
                sign_token(keypair),
                policy_id,
                policy_url,
                database_url,
                migration,
            )
        claims.extensions["siniestros_engine"].dispose()
    policies.extensions["polizas_engine"].dispose()


def assert_contract(payload: object, schema: str) -> None:
    document = yaml.safe_load(
        (ROOT / "contracts" / "openapi" / "bff-web-v1.yaml").read_text(encoding="utf-8")
    )
    OAS31Validator(
        {"$ref": f"#/components/schemas/{schema}", "components": document["components"]}
    ).validate(payload)


def test_registration_list_detail_restart_and_audit(runtime: Runtime) -> None:
    correlation = str(uuid4())
    payload = runtime.payload()
    headers = {**runtime.headers(), "X-Correlation-ID": correlation}
    response = runtime.client.post(PATH, json=payload, headers=headers)
    assert response.status_code == 201, response.get_json()
    detail = response.get_json()
    assert_contract(detail, "DetalleSiniestro")
    assert detail["estado"] == "registrado"
    assert detail["polizaId"] == runtime.policy_id
    assert detail["descripcion"] == payload["descripcion"]
    assert detail["historial"] == [
        {
            "estado": "registrado",
            "fecha": detail["registradoEn"],
            "actorId": str(USER.user_id),
        }
    ]
    assert response.headers["X-Correlation-ID"] == correlation
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Location"] == f"{PATH}/{detail['id']}"
    retry = runtime.client.post(PATH, json=payload, headers=headers)
    assert retry.status_code == 200
    assert retry.get_json() == detail
    listing = runtime.client.get(PATH, headers=bearer(runtime.token))
    assert listing.status_code == 200
    assert_contract(listing.get_json(), "ListadoSiniestros")
    assert listing.get_json()["siniestros"] == [detail]
    assert detail["fechaReporte"] == detail["registradoEn"]
    assert (
        runtime.client.get(f"{PATH}/{detail['id']}", headers=bearer(runtime.token)).get_json()
        == detail
    )
    restarted = create_app(runtime.claims.extensions["siniestros_repository"].config)
    try:
        response = restarted.test_client().get(
            f"{PATH}/{detail['id']}",
            headers={**INTERNAL_HEADERS, "X-Cliente-ID": str(USER.user_id)},
        )
        assert response.status_code == 200
        assert response.get_json()["id"] == detail["id"]
        with Session(runtime.claims.extensions["siniestros_engine"]) as session:
            row: Claim = session.scalars(select(Claim)).one()
            assert row.owner == str(USER.user_id)
            assert row.payload["polizaId"] == runtime.policy_id
            assert row.operation == headers["Idempotency-Key"]
    finally:
        restarted.extensions["siniestros_engine"].dispose()


def test_isolation_and_empty_results(runtime: Runtime) -> None:
    response = runtime.client.post(PATH, json=runtime.payload(), headers=runtime.headers())
    detail = response.get_json()
    other = bearer(runtime.other_token)
    assert runtime.client.get(PATH, headers=other).get_json() == {"siniestros": []}
    not_found = runtime.client.get(f"{PATH}/{detail['id']}", headers=other)
    assert not_found.status_code == 404
    assert detail["descripcion"] not in not_found.get_data(as_text=True)
    assert (
        runtime.client.post(
            PATH, json=runtime.payload(), headers={**other, "Idempotency-Key": str(uuid4())}
        ).status_code
        == 404
    )
    assert (
        runtime.client.post(
            PATH,
            json={**runtime.payload(), "clienteId": str(USER.user_id)},
            headers=runtime.headers(),
        ).status_code
        == 400
    )
    assert (
        runtime.client.get(
            PATH, query_string={"clienteId": str(USER.user_id)}, headers=other
        ).status_code
        == 400
    )


def test_policy_selector_uses_authoritative_owned_current_policies(runtime: Runtime) -> None:
    response = runtime.client.get(PATH + "/polizas", headers=bearer(runtime.token))
    assert response.status_code == 200
    result = response.get_json()
    assert_contract(result, "PolizasParaSiniestro")
    assert len(result["polizas"]) == 1
    policy = result["polizas"][0]
    assert policy["id"] == runtime.policy_id
    assert policy["tipo"] == "automotive" and policy["estado"] == "vigente"
    assert runtime.client.get(
        PATH + "/polizas", headers=bearer(runtime.other_token)
    ).get_json() == {"polizas": []}
    with Session(runtime.policies.extensions["polizas_engine"]) as session:
        row = session.get(PolizaModel, UUID(runtime.policy_id))
        assert row is not None
        row.fecha_fin = datetime.now(UTC).date() - timedelta(days=1)
        session.commit()
    assert runtime.client.get(PATH + "/polizas", headers=bearer(runtime.token)).get_json() == {
        "polizas": []
    }


def test_access_is_rechecked_before_exposing_persisted_claim(runtime: Runtime) -> None:
    payload, headers = runtime.payload(), runtime.headers()
    response = runtime.client.post(PATH, json=payload, headers=headers)
    claim_id = response.get_json()["id"]
    with Session(runtime.policies.extensions["polizas_engine"]) as session:
        row = session.get(PolizaModel, UUID(runtime.policy_id))
        assert row is not None
        row.cliente_id = uuid4()
        session.commit()
    assert (
        runtime.client.get(f"{PATH}/{claim_id}", headers=bearer(runtime.token)).status_code == 404
    )
    assert runtime.client.post(PATH, json=payload, headers=headers).status_code == 404
    assert runtime.client.get(PATH, headers=bearer(runtime.token)).get_json() == {"siniestros": []}


@pytest.mark.parametrize("character", ["x", "\U0001f600"])
def test_maximum_field_lengths_are_persisted_without_truncation(
    runtime: Runtime, character: str
) -> None:
    payload = {
        **runtime.payload(),
        "tipoEvento": character * 100,
        "descripcion": character * 5000,
        "lugar": character * 300,
    }
    response = runtime.client.post(PATH, json=payload, headers=runtime.headers())
    assert response.status_code == 201
    for field in ("tipoEvento", "descripcion", "lugar"):
        assert response.get_json()[field] == payload[field]


@pytest.mark.parametrize(
    "change",
    [
        {"polizaId": "invalida"},
        {"descripcion": " "},
        {"descripcion": "x" * 5001},
        {"tipoEvento": " "},
        {"tipoEvento": "x" * 101},
        {"lugar": ""},
        {"lugar": "x" * 301},
        {"fechaEvento": "2026-11-04T08:00:00"},
        {"fechaEvento": "invalida"},
        {"fechaEvento": 1793799100},
        {"campoNoPermitido": True},
    ],
)
def test_invalid_fields_do_not_persist(runtime: Runtime, change: dict[str, object]) -> None:
    response = runtime.client.post(
        PATH, json={**runtime.payload(), **change}, headers=runtime.headers()
    )
    assert response.status_code == 400
    with Session(runtime.claims.extensions["siniestros_engine"]) as session:
        assert session.scalar(select(func.count()).select_from(Claim)) == 0


def test_invalid_dates_policy_and_idempotency(runtime: Runtime) -> None:
    payload = runtime.payload()
    headers = runtime.headers()
    assert runtime.client.post(PATH, json=payload, headers=bearer(runtime.token)).status_code == 400
    assert (
        runtime.client.post(
            PATH, json={**payload, "polizaId": str(uuid4())}, headers=headers
        ).status_code
        == 404
    )
    for when in (datetime.now(UTC) + timedelta(days=1), datetime.now(UTC) - timedelta(days=2)):
        response = runtime.client.post(
            PATH, json={**payload, "fechaEvento": when.isoformat()}, headers=headers
        )
        assert response.status_code == 400
        assert response.get_json()["code"] == "solicitud_invalida"
    assert runtime.client.post(PATH, json=payload, headers=headers).status_code == 201
    assert (
        runtime.client.post(
            PATH,
            json={**payload, "descripcion": "Descripcion diferente del mismo aviso."},
            headers=headers,
        ).status_code
        == 409
    )
    with Session(runtime.policies.extensions["polizas_engine"]) as session:
        policy = session.get(PolizaModel, UUID(runtime.policy_id))
        assert policy is not None
        policy.fecha_fin = datetime.now(UTC).date() - timedelta(days=1)
        session.commit()
    assert runtime.client.post(PATH, json=payload, headers=headers).status_code == 200
    assert runtime.client.post(PATH, json=payload, headers=runtime.headers()).status_code == 409
    listing = runtime.client.get(PATH, headers=bearer(runtime.token)).get_json()
    assert len(listing["siniestros"]) == 1


def test_concurrent_retries_persist_one_claim(runtime: Runtime) -> None:
    payload, headers = runtime.payload(), runtime.headers()

    def send(_: int) -> tuple[str, int]:
        with runtime.client.application.test_client() as client:
            response = client.post(PATH, json=payload, headers=headers)
            assert response.status_code in {200, 201}, response.get_json()
            return str(response.get_json()["id"]), response.status_code

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(send, range(8)))
        assert len({identifier for identifier, _ in results}) == 1
        assert [status for _, status in results].count(201) == 1
    with Session(runtime.claims.extensions["siniestros_engine"]) as session:
        row: Claim = session.scalars(select(Claim)).one()
        assert len(row.history) == 1


@pytest.mark.parametrize("scheduled", [False, True])
def test_inactive_owned_policy_cannot_start_a_new_operation(
    runtime: Runtime, scheduled: bool
) -> None:
    with Session(runtime.policies.extensions["polizas_engine"]) as session:
        policy = session.get(PolizaModel, UUID(runtime.policy_id))
        assert policy is not None
        if scheduled:
            policy.fecha_inicio = datetime.now(UTC).date() + timedelta(days=1)
        else:
            policy.fecha_fin = datetime.now(UTC).date() - timedelta(days=1)
        session.commit()
    response = runtime.client.post(PATH, json=runtime.payload(), headers=runtime.headers())
    assert response.status_code == 409
    assert response.get_json()["code"] == "poliza_no_vigente"
    assert runtime.client.get(PATH + "/polizas", headers=bearer(runtime.token)).get_json() == {
        "polizas": []
    }


def test_authentication_and_malformed_requests(runtime: Runtime) -> None:
    for headers in ({}, {"Authorization": "Bearer invalido"}):
        assert runtime.client.get(PATH, headers=headers).status_code == 401
        assert runtime.client.post(PATH, json=runtime.payload(), headers=headers).status_code == 401
    assert (
        runtime.claims.test_client()
        .get(PATH, query_string={"clienteId": str(USER.user_id)})
        .status_code
        == 401
    )
    bodies: tuple[object, ...] = (None, [], {"clienteId": []})
    for body in bodies:
        assert runtime.claims.test_client().post(
            PATH, json=body, headers=INTERNAL_HEADERS
        ).status_code in {400, 415}
    assert (
        runtime.client.post(
            PATH, data="{", content_type="application/json", headers=runtime.headers()
        ).status_code
        == 400
    )
    assert runtime.client.get(PATH, json={}, headers=bearer(runtime.token)).status_code == 400
    assert (
        runtime.client.get(f"{PATH}/no-es-uuid", headers=bearer(runtime.token)).status_code == 400
    )
    for path in (PATH, PATH + "/polizas", PATH + "/" + str(uuid4())):
        assert runtime.client.get(path, headers=bearer(runtime.partner_token)).status_code == 403
        assert (
            runtime.client.get(path, headers=bearer(runtime.wrong_scope_token)).status_code == 403
        )
    assert (
        runtime.client.get(
            PATH + "/configuracion-evidencias", headers=bearer(runtime.wrong_scope_token)
        ).status_code
        == 403
    )


def test_lost_confirmation_recovers_without_duplicates(
    runtime: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    @runtime.claims.after_request
    def lose_confirmation(response: Response) -> Response:
        if response.status_code == 201:
            return Response(status=503)
        return response

    payload, headers = runtime.payload(), runtime.headers()
    first = runtime.client.post(PATH, json=payload, headers=headers)
    assert first.status_code == 503
    assert first.get_json()["code"] == "servicio_no_disponible"
    assert first.headers["Retry-After"] == "5"
    monkeypatch.setitem(runtime.claims.after_request_funcs, None, [])
    retry = runtime.client.post(PATH, json=payload, headers=headers)
    assert retry.status_code == 200
    listing = runtime.client.get(PATH, headers=bearer(runtime.token)).get_json()
    assert len(listing["siniestros"]) == 1


def test_dependency_failure_does_not_confirm_or_return_partial_results(
    runtime: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unavailable(_: Response) -> Response:
        return Response(status=503)

    monkeypatch.setitem(runtime.policies.after_request_funcs, None, [unavailable])
    for path in (PATH, f"{PATH}/{uuid4()}"):
        response = runtime.client.get(path, headers=bearer(runtime.token))
        assert response.status_code == 503
        assert "siniestros" not in response.get_json()
    response = runtime.client.post(PATH, json=runtime.payload(), headers=runtime.headers())
    assert response.status_code == 503
    with Session(runtime.claims.extensions["siniestros_engine"]) as session:
        assert session.scalar(select(func.count()).select_from(Claim)) == 0
    monkeypatch.setitem(runtime.policies.after_request_funcs, None, [])


def test_invalid_upstream_payload_is_not_an_empty_list(runtime: Runtime) -> None:
    @runtime.claims.after_request
    def malformed(_: Response) -> Response:
        return jsonify(siniestros=[{"id": str(uuid4())}])

    assert runtime.client.get(PATH, headers=bearer(runtime.token)).status_code == 503


def test_internal_redirect_does_not_forward_credentials(runtime: Runtime) -> None:
    target = Flask("unexpected-target")
    received: list[bool] = []

    @target.get("/redirected")
    def redirected() -> Response:
        received.append(True)
        return jsonify(siniestros=[])

    with serve(target) as target_url:

        @runtime.claims.after_request
        def redirect(_: Response) -> Response:
            return Response(status=302, headers={"Location": target_url + "/redirected"})

        assert runtime.client.get(PATH, headers=bearer(runtime.token)).status_code == 503
    assert received == []


def test_oversized_upstream_response_is_rejected(runtime: Runtime) -> None:
    @runtime.claims.after_request
    def oversized(_: Response) -> Response:
        return jsonify(siniestros=[], extra="x" * (1024 * 1024))

    assert runtime.client.get(PATH, headers=bearer(runtime.token)).status_code == 503


def test_invalid_policy_response_is_not_an_authorization(
    runtime: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    def malformed(_: Response) -> Response:
        return jsonify(polizas=[{"id": runtime.policy_id, "estado": "vigente"}])

    monkeypatch.setitem(runtime.policies.after_request_funcs, None, [malformed])
    assert runtime.client.get(PATH + "/polizas", headers=bearer(runtime.token)).status_code == 503
    assert (
        runtime.client.post(PATH, json=runtime.payload(), headers=runtime.headers()).status_code
        == 503
    )


def test_missing_configuration_is_explicit(
    runtime: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runtime.client.application.extensions["claim_gateway"], "url", "")
    assert runtime.client.get(PATH, headers=bearer(runtime.token)).status_code == 503
    assert runtime.client.get(PATH + "/polizas", headers=bearer(runtime.token)).status_code == 503


def test_database_failure_is_explicit(runtime: Runtime) -> None:
    engine = runtime.claims.extensions["siniestros_engine"]
    Base.metadata.drop_all(engine)
    assert runtime.client.get(PATH, headers=bearer(runtime.token)).status_code == 503
    assert (
        runtime.client.post(PATH, json=runtime.payload(), headers=runtime.headers()).status_code
        == 503
    )


def test_migration_matches_models_and_is_reversible(runtime: Runtime) -> None:
    engine = runtime.claims.extensions["siniestros_engine"]
    with engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    command.downgrade(runtime.migration, "base")
    command.upgrade(runtime.migration, "head")
    with engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []


def test_migration_backfills_existing_claim_without_losing_evidence(runtime: Runtime) -> None:
    engine = runtime.claims.extensions["siniestros_engine"]
    command.downgrade(runtime.migration, "0001_siniestros")
    claim_id, upload_id = str(uuid4()), str(uuid4())
    created = "2026-10-01T10:20:30+00:00"
    buffer = BytesIO()
    with Image.new("RGB", (2, 2), "blue") as image:
        image.save(buffer, format="PNG")
    content = buffer.getvalue()
    storage = runtime.claims.extensions["siniestros_repository"].config.storage_root
    path = storage / upload_id
    path.write_bytes(content)
    claims = Table("siniestros", MetaData(), autoload_with=engine)
    with engine.begin() as connection:
        connection.execute(
            claims.insert().values(
                id=claim_id,
                owner=str(USER.user_id),
                operation=str(uuid4()),
                fingerprint="a" * 64,
                payload=runtime.payload(),
                created=created,
            )
        )
        connection.execute(
            Upload.__table__.insert().values(
                id=upload_id,
                claim_id=claim_id,
                owner=str(USER.user_id),
                operation=str(uuid4()),
                fingerprint="b" * 64,
                mime="image/png",
                size=len(content),
                digest=hashlib.sha256(content).hexdigest(),
                offset=len(content),
                status="confirmada",
                created=created,
                confirmed=created,
            )
        )
    command.upgrade(runtime.migration, "head")
    with Session(engine) as session:
        claim = session.get(Claim, claim_id)
        upload = session.get(Upload, upload_id)
        assert claim is not None and claim.state == "registrado"
        assert claim.history == [
            {
                "estado": "registrado",
                "fecha": created,
                "actorId": str(USER.user_id),
            }
        ]
        assert upload is not None and upload.claim_id == claim_id
        assert upload.status == "confirmada" and upload.offset == len(content)
        assert path.read_bytes() == content
    response = runtime.client.get(f"{PATH}/{claim_id}", headers=bearer(runtime.token))
    assert response.status_code == 200
    assert response.get_json()["registradoEn"] == "2026-10-01T10:20:30Z"
    assert response.get_json()["historial"][0]["fecha"] == "2026-10-01T10:20:30Z"
    download = runtime.client.get(
        f"{PATH}/{claim_id}/evidencias/{upload_id}/contenido", headers=bearer(runtime.token)
    )
    assert download.status_code == 200 and download.data == content
    assert download.headers["Content-Type"] == "image/png"
