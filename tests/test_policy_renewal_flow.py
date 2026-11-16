import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from threading import Thread
from typing import Any
from uuid import UUID, uuid4

import pytest
import yaml
from alembic import command
from alembic.config import Config as AlembicConfig
from bff_web.app import create_app as create_bff
from bff_web.config import Config
from bff_web.domain.auth import User
from bff_web.infrastructure.jwt_verifier import JwksKeyStore, JwtVerifier
from cryptography.hazmat.primitives.asymmetric import rsa
from flask.testing import FlaskClient
from jwt.algorithms import RSAAlgorithm
from openapi_schema_validator import OAS31Validator
from polizas.app import create_app as create_policies
from polizas.infrastructure.models import PagoRenovacionModel
from polizas.infrastructure.renovaciones import ResultadoPago, SimuladorPagoRenovacion
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from werkzeug.serving import make_server

from tests import test_dashboard_flow as dashboard_flow
from tests.test_dashboard_flow import ISSUER, ROOT, USER, IdentityGatewayStub, sign_token

keypair = dashboard_flow.keypair
INTERNAL_TOKEN = "renovacion-test-service-credential"
CONTRACT = yaml.safe_load((ROOT / "contracts" / "openapi" / "bff-web-v1.yaml").read_text())


class Clock:
    def __init__(self) -> None:
        self.now = datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.now

    def on(self, day: date) -> None:
        self.now = datetime.combine(day, time(15), UTC)


class LostResponses:
    def __init__(self, simulator: SimuladorPagoRenovacion) -> None:
        self.simulator = simulator
        self.failures = 0

    def cobrar(self, renovacion_id: UUID, monto: str, moneda: str) -> ResultadoPago:
        if self.failures:
            self.failures -= 1
            raise TimeoutError("respuesta perdida")
        return self.simulator.cobrar(renovacion_id, monto, moneda)


@dataclass
class Runtime:
    bff: FlaskClient
    policies: FlaskClient
    clock: Clock
    gateway: LostResponses
    database_url: str


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
    jwk = json.loads(RSAAlgorithm.to_jwk(keypair.public_key()))
    jwk.update(kid="dashboard-test", use="sig", alg="RS256")
    bff = create_bff(
        Config(
            identity_url="http://identidad.test",
            jwt_issuer=ISSUER,
            internal_api_token=INTERNAL_TOKEN,
            policies_service_url=f"http://127.0.0.1:{server.server_port}",
        ),
        identity_gateway=IdentityGatewayStub(),
        token_verifier=JwtVerifier(
            JwksKeyStore(lambda: {"keys": [jwk]}), ISSUER, "solventa-api", "web"
        ),
    )
    try:
        yield Runtime(bff.test_client(), policies.test_client(), clock, gateway, url)
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        policies.extensions["polizas_engine"].dispose()


def validate(schema: str, payload: object) -> None:
    document = {"$ref": f"#/components/schemas/{schema}", "components": CONTRACT["components"]}
    OAS31Validator(document, format_checker=OAS31Validator.FORMAT_CHECKER).validate(payload)


def issue_policy(runtime: Runtime, user: User = USER) -> dict[str, Any]:
    response = runtime.policies.post(
        "/v1/polizas",
        json={
            "clienteId": str(user.user_id),
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
    policy: dict[str, Any] = response.get_json()
    return policy


def headers(token: str, key: str | None = None) -> dict[str, str]:
    values = {"Authorization": f"Bearer {token}"}
    if key is not None:
        values["Idempotency-Key"] = key
    return values


def payments(runtime: Runtime) -> int:
    with Session(create_engine(runtime.database_url)) as session:
        return int(session.scalar(select(func.count()).select_from(PagoRenovacionModel)) or 0)


def test_cliente_renueva_desde_web_con_contrato_publicado(
    runtime: Runtime, keypair: rsa.RSAPrivateKey
) -> None:
    token = sign_token(keypair, scope="cliente")
    policy = issue_policy(runtime)
    path = f"/v1/clientes/me/polizas/{policy['id']}"

    early = runtime.bff.get(path + "/renovacion", headers=headers(token))
    assert early.status_code == 200
    validate("ElegibilidadRenovacion", early.get_json())
    assert early.get_json()["motivo"] == "fuera_de_ventana"

    runtime.clock.on(date.fromisoformat(policy["fechaFin"]) - timedelta(days=1))
    eligibility = runtime.bff.get(path + "/renovacion", headers=headers(token))
    assert eligibility.headers["Cache-Control"] == "no-store"
    validate("ElegibilidadRenovacion", eligibility.get_json())
    offer = eligibility.get_json()["oferta"]
    assert offer["prima"]["monto"] == "25000"

    body = {"aceptaCondiciones": True, "condicionesHash": offer["condicionesHash"]}
    key = str(uuid4())
    renewed = runtime.bff.post(path + "/renovaciones", json=body, headers=headers(token, key))
    assert renewed.status_code == 201
    validate("RenovacionPoliza", renewed.get_json())
    assert renewed.get_json()["estado"] == "renovada"
    replay = runtime.bff.post(path + "/renovaciones", json=body, headers=headers(token, key))
    assert replay.status_code == 200 and replay.get_json() == renewed.get_json()

    after = runtime.bff.get(path + "/renovacion", headers=headers(token)).get_json()
    assert after["vigenciaActual"] == {
        "inicio": policy["fechaInicio"],
        "fin": offer["nuevaVigencia"]["fin"],
    }
    assert after["ultimoIntento"] == renewed.get_json()
    assert payments(runtime) == 1


def test_errores_de_negocio_se_publican_como_problemas(
    runtime: Runtime, keypair: rsa.RSAPrivateKey
) -> None:
    token = sign_token(keypair, scope="cliente")
    policy = issue_policy(runtime)
    path = f"/v1/clientes/me/polizas/{policy['id']}/renovaciones"
    body = {"aceptaCondiciones": True, "condicionesHash": "a" * 64}

    outside = runtime.bff.post(path, json=body, headers=headers(token, str(uuid4())))
    assert outside.status_code == 422
    validate("Problema", outside.get_json())
    assert outside.get_json()["motivo"] == "fuera_de_ventana"

    runtime.clock.on(date.fromisoformat(policy["fechaFin"]))
    changed = runtime.bff.post(path, json=body, headers=headers(token, str(uuid4())))
    assert changed.status_code == 409
    assert changed.get_json()["code"] == "condiciones_cambiaron"
    validate("Problema", changed.get_json())

    not_accepted = runtime.bff.post(
        path, json={**body, "aceptaCondiciones": False}, headers=headers(token, str(uuid4()))
    )
    assert not_accepted.status_code == 400
    assert runtime.bff.post(path, json=body, headers=headers(token)).status_code == 400

    other = sign_token(
        keypair, User(uuid4(), "Otra", "otra@solventa.co", "cliente"), scope="cliente"
    )
    foreign = runtime.bff.get(
        f"/v1/clientes/me/polizas/{policy['id']}/renovacion", headers=headers(other)
    )
    assert foreign.status_code == 404
    validate("Problema", foreign.get_json())
    assert runtime.bff.get(f"/v1/clientes/me/polizas/{policy['id']}/renovacion").status_code == 401
    assert payments(runtime) == 0


def test_pago_ambiguo_se_recupera_con_la_misma_clave(
    runtime: Runtime, keypair: rsa.RSAPrivateKey
) -> None:
    token = sign_token(keypair, scope="cliente")
    policy = issue_policy(runtime)
    runtime.clock.on(date.fromisoformat(policy["fechaFin"]))
    path = f"/v1/clientes/me/polizas/{policy['id']}"
    offer = runtime.bff.get(path + "/renovacion", headers=headers(token)).get_json()["oferta"]
    body = {"aceptaCondiciones": True, "condicionesHash": offer["condicionesHash"]}
    key = str(uuid4())
    runtime.gateway.failures = 1

    pending = runtime.bff.post(path + "/renovaciones", json=body, headers=headers(token, key))
    assert pending.status_code == 202
    assert pending.headers["Retry-After"] == "5"
    validate("RenovacionPoliza", pending.get_json())
    competing = runtime.bff.post(
        path + "/renovaciones", json=body, headers=headers(token, str(uuid4()))
    )
    assert competing.status_code == 409
    assert competing.get_json()["renovacionId"] == pending.get_json()["id"]
    validate("Problema", competing.get_json())

    recovered = runtime.bff.post(path + "/renovaciones", json=body, headers=headers(token, key))
    assert recovered.status_code == 200
    assert recovered.get_json()["estado"] == "renovada"
    assert payments(runtime) == 1


def test_bff_sin_polizas_configurado_no_confirma(keypair: rsa.RSAPrivateKey) -> None:
    jwk = json.loads(RSAAlgorithm.to_jwk(keypair.public_key()))
    jwk.update(kid="dashboard-test", use="sig", alg="RS256")
    bff = create_bff(
        Config(
            identity_url="http://identidad.test",
            jwt_issuer=ISSUER,
            internal_api_token=INTERNAL_TOKEN,
        ),
        identity_gateway=IdentityGatewayStub(),
        token_verifier=JwtVerifier(
            JwksKeyStore(lambda: {"keys": [jwk]}), ISSUER, "solventa-api", "web"
        ),
    ).test_client()
    token = sign_token(keypair, scope="cliente")
    response = bff.get(f"/v1/clientes/me/polizas/{uuid4()}/renovacion", headers=headers(token))
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "5"
