import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
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
from bff_movil.config import Config
from bff_movil.infrastructure.jwt_verifier import JwksKeyStore, JwtVerifier
from cryptography.hazmat.primitives.asymmetric import rsa
from flask.testing import FlaskClient
from jwt.algorithms import RSAAlgorithm
from openapi_schema_validator import OAS31Validator
from polizas.app import create_app as create_policies
from polizas.infrastructure.models import PagoRenovacionModel, PolizaModel
from polizas.infrastructure.renovaciones import SimuladorPagoRenovacion
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from werkzeug.serving import make_server

from tests import test_dashboard_flow as dashboard_flow
from tests.test_dashboard_flow import ISSUER, ROOT, USER
from tests.test_policy_renewal_flow import Clock, LostResponses

keypair = dashboard_flow.keypair
INTERNAL_TOKEN = "renovacion-movil-test-service-credential"
CONTRACTS = ROOT / "contracts" / "openapi"
MOBILE = "bff-movil-v1.yaml"
PREFIX = "/movil/v1/polizas"
AUDIENCE = "solventa-api"


def load(name: str) -> dict[str, Any]:
    document: dict[str, Any] = yaml.safe_load((CONTRACTS / name).read_text(encoding="utf-8"))
    return document


def resolve(node: dict[str, Any], document: str) -> tuple[dict[str, Any], str]:
    while "$ref" in node:
        target, _, pointer = str(node["$ref"]).partition("#")
        document = Path(target).name if target else document
        node = load(document)
        for part in pointer.strip("/").split("/"):
            node = node[part.replace("~1", "/").replace("~0", "~")]
    return node, document


def validate_response(path: str, method: str, status: int, payload: object) -> None:
    """Valida contra el contrato movil publicado, siguiendo sus $ref entre archivos."""
    operation = load(MOBILE)["paths"][path][method]
    response, document = resolve(operation["responses"][str(status)], MOBILE)
    media = "application/problem+json" if status >= 400 else "application/json"
    schema, document = resolve(response["content"][media]["schema"], document)
    validator_document = {**schema, "components": load(document)["components"]}
    OAS31Validator(validator_document, format_checker=OAS31Validator.FORMAT_CHECKER).validate(
        payload
    )


def sign_mobile_token(
    key: rsa.RSAPrivateKey,
    *,
    user_id: UUID = USER.user_id,
    role: str = "cliente",
    scope: str = "cliente",
    client_id: str = "movil",
) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(user_id),
            "iss": ISSUER,
            "aud": AUDIENCE,
            "iat": now,
            "exp": now + timedelta(hours=1),
            "jti": str(uuid4()),
            "client_id": client_id,
            "scope": scope,
            "nombre": "Cliente",
            "correo": "ana@solventa.co",
            "rol": role,
        },
        key,
        algorithm="RS256",
        headers={"kid": "dashboard-test"},
    )


@dataclass
class Runtime:
    mobile: FlaskClient
    policies: FlaskClient
    clock: Clock
    gateway: LostResponses
    database_url: str


def mobile_app(key: rsa.RSAPrivateKey, policies_url: str = "") -> FlaskClient:
    jwk = json.loads(RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid="dashboard-test", use="sig", alg="RS256")
    client: FlaskClient = create_mobile(
        Config(
            identity_url="http://identidad.test",
            jwt_issuer=ISSUER,
            internal_api_token=INTERNAL_TOKEN,
            policies_service_url=policies_url,
        ),
        token_verifier=JwtVerifier(
            JwksKeyStore(lambda: {"keys": [jwk]}), ISSUER, AUDIENCE, "movil"
        ),
    ).test_client()
    return client


@pytest.fixture
def runtime(tmp_path: Path, keypair: rsa.RSAPrivateKey) -> Iterator[Runtime]:
    url = f"sqlite:///{tmp_path / 'polizas.db'}"
    migrations = AlembicConfig(str(ROOT / "services" / "polizas" / "alembic.ini"))
    migrations.set_main_option("sqlalchemy.url", url)
    command.upgrade(migrations, "head")
    clock = Clock()
    gateway = LostResponses(SimuladorPagoRenovacion(sessionmaker(create_engine(url)), "confirmado"))
    policies = create_policies(url, INTERNAL_TOKEN, pasarela=gateway, reloj=clock)
    server = make_server("127.0.0.1", 0, policies, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    mobile = mobile_app(keypair, f"http://127.0.0.1:{server.server_port}")
    try:
        yield Runtime(mobile, policies.test_client(), clock, gateway, url)
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        policies.extensions["polizas_engine"].dispose()


def issue_policy(runtime: Runtime) -> dict[str, Any]:
    response = runtime.policies.post(
        "/v1/polizas",
        json={
            "clienteId": str(USER.user_id),
            "suscripcionId": str(uuid4()),
            "pagoId": str(uuid4()),
            "tipo": "life",
            "confirmadaEn": datetime.now(UTC).isoformat(),
            "simulada": True,
            "fechasContratadas": None,
        },
        headers={"Authorization": "Bearer " + INTERNAL_TOKEN},
    )
    assert response.status_code == 200
    policy: dict[str, Any] = response.get_json()
    return policy


def headers(token: str, key: str | None = None) -> dict[str, str]:
    values = {"Authorization": "Bearer " + token}
    if key is not None:
        values["Idempotency-Key"] = key
    return values


def payments(runtime: Runtime) -> int:
    with Session(create_engine(runtime.database_url)) as session:
        return int(session.scalar(select(func.count()).select_from(PagoRenovacionModel)) or 0)


def policy_end(runtime: Runtime, policy_id: str) -> date:
    with Session(create_engine(runtime.database_url)) as session:
        stored = session.get(PolizaModel, UUID(policy_id))
        assert stored is not None
        end: date = stored.fecha_fin
        return end


ELIGIBILITY = "/v1/polizas/{polizaId}/renovacion"
RENEWALS = "/v1/polizas/{polizaId}/renovaciones"


def test_cliente_renueva_desde_la_app_movil_con_contrato_publicado(
    runtime: Runtime, keypair: rsa.RSAPrivateKey
) -> None:
    token = sign_mobile_token(keypair)
    policy = issue_policy(runtime)
    path = f"{PREFIX}/{policy['id']}"

    early = runtime.mobile.get(path + "/renovacion", headers=headers(token))
    assert early.status_code == 200
    validate_response(ELIGIBILITY, "get", 200, early.get_json())
    assert early.get_json()["renovable"] is False
    assert early.get_json()["motivo"] == "fuera_de_ventana"

    runtime.clock.on(date.fromisoformat(policy["fechaFin"]) - timedelta(days=1))
    eligibility = runtime.mobile.get(path + "/renovacion", headers=headers(token))
    assert eligibility.status_code == 200
    assert eligibility.headers["Cache-Control"] == "no-store"
    validate_response(ELIGIBILITY, "get", 200, eligibility.get_json())
    offer = eligibility.get_json()["oferta"]
    assert (
        offer["nuevaVigencia"]["inicio"]
        == (date.fromisoformat(policy["fechaFin"]) + timedelta(days=1)).isoformat()
    )
    assert offer["coberturas"] and offer["condiciones"]
    assert offer["prima"]["monto"] == "25000"

    body = {"aceptaCondiciones": True, "condicionesHash": offer["condicionesHash"]}
    key = str(uuid4())
    renewed = runtime.mobile.post(path + "/renovaciones", json=body, headers=headers(token, key))
    assert renewed.status_code == 201
    validate_response(RENEWALS, "post", 201, renewed.get_json())
    assert renewed.get_json()["estado"] == "renovada"
    replay = runtime.mobile.post(path + "/renovaciones", json=body, headers=headers(token, key))
    assert replay.status_code == 200 and replay.get_json() == renewed.get_json()
    validate_response(RENEWALS, "post", 200, replay.get_json())

    after = runtime.mobile.get(path + "/renovacion", headers=headers(token)).get_json()
    assert after["vigenciaActual"] == {
        "inicio": policy["fechaInicio"],
        "fin": offer["nuevaVigencia"]["fin"],
    }
    assert after["ultimoIntento"] == renewed.get_json()
    assert payments(runtime) == 1


def test_rechazos_no_alteran_la_poliza_original(
    runtime: Runtime, keypair: rsa.RSAPrivateKey
) -> None:
    token = sign_mobile_token(keypair)
    policy = issue_policy(runtime)
    original_end = policy_end(runtime, policy["id"])
    path = f"{PREFIX}/{policy['id']}/renovaciones"
    body = {"aceptaCondiciones": True, "condicionesHash": "a" * 64}

    outside = runtime.mobile.post(path, json=body, headers=headers(token, str(uuid4())))
    assert outside.status_code == 422
    validate_response(RENEWALS, "post", 422, outside.get_json())
    assert outside.get_json()["motivo"] == "fuera_de_ventana"

    runtime.clock.on(original_end)
    changed = runtime.mobile.post(path, json=body, headers=headers(token, str(uuid4())))
    assert changed.status_code == 409
    assert changed.get_json()["code"] == "condiciones_cambiaron"
    validate_response(RENEWALS, "post", 409, changed.get_json())

    not_accepted = runtime.mobile.post(
        path, json={**body, "aceptaCondiciones": False}, headers=headers(token, str(uuid4()))
    )
    assert not_accepted.status_code == 400
    validate_response(RENEWALS, "post", 400, not_accepted.get_json())

    foreign = runtime.mobile.get(
        f"{PREFIX}/{policy['id']}/renovacion",
        headers=headers(sign_mobile_token(keypair, user_id=uuid4())),
    )
    assert foreign.status_code == 404
    validate_response(ELIGIBILITY, "get", 404, foreign.get_json())

    web_token = sign_mobile_token(keypair, client_id="web")
    unauthorized = runtime.mobile.get(
        f"{PREFIX}/{policy['id']}/renovacion", headers=headers(web_token)
    )
    assert unauthorized.status_code == 401
    forbidden = runtime.mobile.get(
        f"{PREFIX}/{policy['id']}/renovacion",
        headers=headers(sign_mobile_token(keypair, scope="cotizaciones")),
    )
    assert forbidden.status_code == 403
    validate_response(ELIGIBILITY, "get", 403, forbidden.get_json())

    assert payments(runtime) == 0
    assert policy_end(runtime, policy["id"]) == original_end


def test_pago_ambiguo_queda_pendiente_identificable_y_se_recupera_sin_doble_cobro(
    runtime: Runtime, keypair: rsa.RSAPrivateKey
) -> None:
    token = sign_mobile_token(keypair)
    policy = issue_policy(runtime)
    original_end = policy_end(runtime, policy["id"])
    runtime.clock.on(original_end)
    path = f"{PREFIX}/{policy['id']}"
    offer = runtime.mobile.get(path + "/renovacion", headers=headers(token)).get_json()["oferta"]
    body = {"aceptaCondiciones": True, "condicionesHash": offer["condicionesHash"]}
    key = str(uuid4())
    runtime.gateway.failures = 1

    pending = runtime.mobile.post(path + "/renovaciones", json=body, headers=headers(token, key))
    assert pending.status_code == 202
    assert pending.headers["Retry-After"] == "5"
    validate_response(RENEWALS, "post", 202, pending.get_json())
    assert pending.get_json()["estado"] == "pendiente"
    assert policy_end(runtime, policy["id"]) == original_end

    status = runtime.mobile.get(path + "/renovacion", headers=headers(token)).get_json()
    assert status["ultimoIntento"]["id"] == pending.get_json()["id"]
    assert status["ultimoIntento"]["estado"] == "pendiente"

    competing = runtime.mobile.post(
        path + "/renovaciones", json=body, headers=headers(token, str(uuid4()))
    )
    assert competing.status_code == 409
    assert competing.get_json()["code"] == "renovacion_en_curso"
    assert competing.get_json()["renovacionId"] == pending.get_json()["id"]
    validate_response(RENEWALS, "post", 409, competing.get_json())

    recovered = runtime.mobile.post(path + "/renovaciones", json=body, headers=headers(token, key))
    assert recovered.status_code == 200
    assert recovered.get_json()["id"] == pending.get_json()["id"]
    assert recovered.get_json()["estado"] == "renovada"
    again = runtime.mobile.post(path + "/renovaciones", json=body, headers=headers(token, key))
    assert again.status_code == 200 and again.get_json() == recovered.get_json()
    assert payments(runtime) == 1
    assert policy_end(runtime, policy["id"]) == date.fromisoformat(offer["nuevaVigencia"]["fin"])


def test_bff_movil_sin_polizas_configurado_no_confirma(keypair: rsa.RSAPrivateKey) -> None:
    mobile = mobile_app(keypair)
    token = sign_mobile_token(keypair)

    response = mobile.post(
        f"{PREFIX}/{uuid4()}/renovaciones",
        json={"aceptaCondiciones": True, "condicionesHash": "a" * 64},
        headers=headers(token, str(uuid4())),
    )
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "5"
    validate_response(RENEWALS, "post", 503, response.get_json())
