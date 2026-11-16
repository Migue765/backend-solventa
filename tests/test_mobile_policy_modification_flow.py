import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from threading import Thread
from uuid import UUID, uuid4

import jwt
import pytest
import yaml
from alembic import command
from alembic.config import Config as AlembicConfig
from bff_movil.app import create_app as create_mobile
from bff_movil.config import Config as MobileConfig
from bff_movil.infrastructure.jwt_verifier import JwksKeyStore, JwtVerifier
from cryptography.hazmat.primitives.asymmetric import rsa
from flask.testing import FlaskClient
from jwt.algorithms import RSAAlgorithm
from openapi_schema_validator import OAS31Validator
from polizas.app import create_app as create_policies
from polizas.infrastructure.models import AuditoriaPolizaModel, VersionPolizaModel
from polizas.infrastructure.renovaciones import SimuladorPagoRenovacion
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from werkzeug.serving import make_server

from tests import test_dashboard_flow as dashboard_flow
from tests.test_dashboard_flow import ISSUER, ROOT, USER
from tests.test_policy_renewal_flow import INTERNAL_TOKEN, Clock

keypair = dashboard_flow.keypair
CONTRACT = yaml.safe_load(
    (ROOT / "contracts" / "openapi" / "modificacion-poliza-movil-v1.yaml").read_text(
        encoding="utf-8"
    )
)
CHANGE = {"versionBase": 1, "plan": "premium", "coberturasOpcionales": []}


@dataclass
class Runtime:
    movil: FlaskClient
    policies: FlaskClient
    clock: Clock
    database_url: str


@pytest.fixture
def runtime(tmp_path: Path, keypair: rsa.RSAPrivateKey) -> Iterator[Runtime]:
    url = f"sqlite:///{tmp_path / 'polizas.db'}"
    migrations = AlembicConfig(str(ROOT / "services" / "polizas" / "alembic.ini"))
    migrations.set_main_option("sqlalchemy.url", url)
    command.upgrade(migrations, "head")
    clock = Clock()
    payments = SimuladorPagoRenovacion(sessionmaker(create_engine(url)), "confirmado")
    policies = create_policies(url, INTERNAL_TOKEN, pasarela=payments, reloj=clock)
    server = make_server("127.0.0.1", 0, policies, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    jwk = json.loads(RSAAlgorithm.to_jwk(keypair.public_key()))
    jwk.update(kid="movil-test", use="sig", alg="RS256")
    movil = create_mobile(
        MobileConfig(
            identity_url="http://identidad.test",
            jwt_issuer=ISSUER,
            internal_api_token=INTERNAL_TOKEN,
            policies_service_url=f"http://127.0.0.1:{server.server_port}",
        ),
        token_verifier=JwtVerifier(
            JwksKeyStore(lambda: {"keys": [jwk]}), ISSUER, "solventa-api", "movil"
        ),
    )
    try:
        yield Runtime(movil.test_client(), policies.test_client(), clock, url)
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        policies.extensions["polizas_engine"].dispose()


def mobile_token(key: rsa.RSAPrivateKey, user_id: UUID = USER.user_id) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(user_id),
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
        headers={"kid": "movil-test"},
    )


def headers(token: str, key: str | None = None) -> dict[str, str]:
    values = {"Authorization": f"Bearer {token}"}
    if key is not None:
        values["Idempotency-Key"] = key
    return values


def validate(schema: str, payload: object) -> None:
    document = {"$ref": f"#/components/schemas/{schema}", "components": CONTRACT["components"]}
    OAS31Validator(document, format_checker=OAS31Validator.FORMAT_CHECKER).validate(payload)


def issue_policy(runtime: Runtime) -> dict[str, str]:
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
        headers={"Authorization": f"Bearer {INTERNAL_TOKEN}"},
    )
    assert response.status_code == 200
    policy: dict[str, str] = response.get_json()
    return policy


def count(runtime: Runtime, model: type[object]) -> int:
    with Session(create_engine(runtime.database_url)) as session:
        return int(session.scalar(select(func.count()).select_from(model)) or 0)


def test_app_movil_simula_confirma_y_versiona_la_poliza(
    runtime: Runtime, keypair: rsa.RSAPrivateKey
) -> None:
    token = mobile_token(keypair)
    policy = issue_policy(runtime)
    runtime.clock.on(date.fromisoformat(policy["fechaInicio"]) + timedelta(days=3))
    path = f"/movil/v1/polizas/{policy['id']}"

    state = runtime.movil.get(path + "/modificacion", headers=headers(token))
    assert state.status_code == 200
    assert state.headers["Cache-Control"] == "no-store"
    validate("EstadoModificacionPoliza", state.get_json())
    assert state.get_json()["modificable"] is True

    simulated = runtime.movil.post(
        path + "/modificaciones/simulacion", json=CHANGE, headers=headers(token)
    )
    assert simulated.status_code == 200
    impact = simulated.get_json()
    validate("ImpactoModificacion", impact)
    assert impact["primaNueva"]["monto"] == "24700"
    assert impact["coberturasRetiradas"] == ["incapacidad_total"]
    assert count(runtime, VersionPolizaModel) == 0

    body = {**CHANGE, "confirma": True, "impactoHash": impact["impactoHash"]}
    validate("ConfirmacionModificacion", body)
    key = str(uuid4())
    confirmed = runtime.movil.post(path + "/modificaciones", json=body, headers=headers(token, key))
    assert confirmed.status_code == 201
    validate("ModificacionPoliza", confirmed.get_json())
    replay = runtime.movil.post(path + "/modificaciones", json=body, headers=headers(token, key))
    assert replay.status_code == 200 and replay.get_json() == confirmed.get_json()

    after = runtime.movil.get(path + "/modificacion", headers=headers(token)).get_json()
    validate("EstadoModificacionPoliza", after)
    assert [item["version"] for item in after["historial"]] == [2, 1]
    assert after["versionActual"] == confirmed.get_json()["version"]
    assert count(runtime, VersionPolizaModel) == 2


def test_rechazos_indican_causa_recuperable_sin_cambios_parciales(
    runtime: Runtime, keypair: rsa.RSAPrivateKey
) -> None:
    token = mobile_token(keypair)
    policy = issue_policy(runtime)
    runtime.clock.on(date.fromisoformat(policy["fechaInicio"]) + timedelta(days=1))
    path = f"/movil/v1/polizas/{policy['id']}/modificaciones"

    not_allowed = runtime.movil.post(
        path + "/simulacion",
        json={**CHANGE, "coberturasOpcionales": ["fallecimiento"]},
        headers=headers(token),
    )
    assert not_allowed.status_code == 422
    validate("ProblemaModificacion", not_allowed.get_json())
    assert not_allowed.get_json()["campo"] == "coberturasOpcionales.fallecimiento"
    assert not_allowed.get_json()["accion"] == "ajustar_cambios"

    stale = runtime.movil.post(
        path,
        json={**CHANGE, "confirma": True, "impactoHash": "a" * 64},
        headers=headers(token, str(uuid4())),
    )
    assert stale.status_code == 409
    validate("ProblemaModificacion", stale.get_json())
    assert stale.get_json()["accion"] == "simular_de_nuevo"

    outdated = runtime.movil.post(
        path + "/simulacion", json={**CHANGE, "versionBase": 2}, headers=headers(token)
    )
    assert outdated.status_code == 409
    validate("ProblemaModificacion", outdated.get_json())
    assert outdated.get_json()["versionActual"] == 1
    assert outdated.get_json()["accion"] == "consultar_poliza"

    foreign = runtime.movil.post(
        path + "/simulacion", json=CHANGE, headers=headers(mobile_token(keypair, uuid4()))
    )
    assert foreign.status_code == 404

    assert count(runtime, VersionPolizaModel) == 0
    assert count(runtime, AuditoriaPolizaModel) == 1


def test_poliza_fuera_de_vigencia_no_es_modificable(
    runtime: Runtime, keypair: rsa.RSAPrivateKey
) -> None:
    token = mobile_token(keypair)
    policy = issue_policy(runtime)
    runtime.clock.on(date.fromisoformat(policy["fechaFin"]) + timedelta(days=1))
    path = f"/movil/v1/polizas/{policy['id']}"

    state = runtime.movil.get(path + "/modificacion", headers=headers(token))
    assert state.status_code == 200
    validate("EstadoModificacionPoliza", state.get_json())
    assert state.get_json()["modificable"] is False

    rejected = runtime.movil.post(
        path + "/modificaciones/simulacion", json=CHANGE, headers=headers(token)
    )
    assert rejected.status_code == 422
    validate("ProblemaModificacion", rejected.get_json())
    assert rejected.get_json()["code"] == "poliza_no_modificable"
