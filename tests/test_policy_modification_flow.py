from datetime import date, timedelta
from uuid import uuid4

from bff_web.domain.auth import User
from cryptography.hazmat.primitives.asymmetric import rsa
from polizas.infrastructure.models import AuditoriaPolizaModel, VersionPolizaModel
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from tests import test_policy_renewal_flow as renewal_flow
from tests.test_dashboard_flow import sign_token
from tests.test_policy_renewal_flow import Runtime, headers, issue_policy, validate

keypair = renewal_flow.keypair
runtime = renewal_flow.runtime

CHANGE = {"versionBase": 1, "plan": "premium", "coberturasOpcionales": []}


def count(runtime: Runtime, model: type[object]) -> int:
    with Session(create_engine(runtime.database_url)) as session:
        return int(session.scalar(select(func.count()).select_from(model)) or 0)


def test_cliente_simula_confirma_y_consulta_historial(
    runtime: Runtime, keypair: rsa.RSAPrivateKey
) -> None:
    token = sign_token(keypair, scope="cliente")
    policy = issue_policy(runtime)
    runtime.clock.on(date.fromisoformat(policy["fechaInicio"]) + timedelta(days=3))
    path = f"/v1/clientes/me/polizas/{policy['id']}"

    state = runtime.bff.get(path + "/modificacion", headers=headers(token))
    assert state.status_code == 200
    assert state.headers["Cache-Control"] == "no-store"
    validate("EstadoModificacionPoliza", state.get_json())
    assert state.get_json()["versionActual"]["prima"]["monto"] == "25000"

    simulated = runtime.bff.post(
        path + "/modificaciones/simulacion", json=CHANGE, headers=headers(token)
    )
    assert simulated.status_code == 200
    impact = simulated.get_json()
    validate("ImpactoModificacion", impact)
    assert impact["primaNueva"]["monto"] == "24700"
    assert impact["coberturasRetiradas"] == ["incapacidad_total"]

    body = {**CHANGE, "confirma": True, "impactoHash": impact["impactoHash"]}
    validate("ConfirmacionModificacion", body)
    key = str(uuid4())
    confirmed = runtime.bff.post(path + "/modificaciones", json=body, headers=headers(token, key))
    assert confirmed.status_code == 201
    validate("ModificacionPoliza", confirmed.get_json())
    replay = runtime.bff.post(path + "/modificaciones", json=body, headers=headers(token, key))
    assert replay.status_code == 200 and replay.get_json() == confirmed.get_json()

    after = runtime.bff.get(path + "/modificacion", headers=headers(token)).get_json()
    validate("EstadoModificacionPoliza", after)
    assert [item["version"] for item in after["historial"]] == [2, 1]
    assert after["versionActual"] == confirmed.get_json()["version"]
    assert count(runtime, VersionPolizaModel) == 2


def test_rechazos_se_publican_como_problemas_y_no_aplican_cambios(
    runtime: Runtime, keypair: rsa.RSAPrivateKey
) -> None:
    token = sign_token(keypair, scope="cliente")
    policy = issue_policy(runtime)
    runtime.clock.on(date.fromisoformat(policy["fechaInicio"]) + timedelta(days=1))
    path = f"/v1/clientes/me/polizas/{policy['id']}/modificaciones"

    not_allowed = runtime.bff.post(
        path + "/simulacion",
        json={**CHANGE, "coberturasOpcionales": ["fallecimiento"]},
        headers=headers(token),
    )
    assert not_allowed.status_code == 422
    validate("Problema", not_allowed.get_json())
    assert not_allowed.get_json()["campo"] == "coberturasOpcionales.fallecimiento"

    same = {"versionBase": 1, "plan": "esencial", "coberturasOpcionales": ["incapacidad_total"]}
    unchanged = runtime.bff.post(path + "/simulacion", json=same, headers=headers(token))
    assert unchanged.status_code == 422 and unchanged.get_json()["code"] == "sin_cambios"

    stale = runtime.bff.post(
        path,
        json={**CHANGE, "confirma": True, "impactoHash": "a" * 64},
        headers=headers(token, str(uuid4())),
    )
    assert stale.status_code == 409 and stale.get_json()["code"] == "impacto_cambio"
    validate("Problema", stale.get_json())

    outdated = runtime.bff.post(
        path + "/simulacion", json={**CHANGE, "versionBase": 2}, headers=headers(token)
    )
    assert outdated.status_code == 409 and outdated.get_json()["versionActual"] == 1

    unconfirmed = runtime.bff.post(
        path,
        json={**CHANGE, "confirma": False, "impactoHash": "a" * 64},
        headers=headers(token, str(uuid4())),
    )
    assert unconfirmed.status_code == 400

    other = sign_token(
        keypair, User(uuid4(), "Otra", "otra@solventa.co", "cliente"), scope="cliente"
    )
    foreign = runtime.bff.post(path + "/simulacion", json=CHANGE, headers=headers(other))
    assert foreign.status_code == 404

    assert count(runtime, VersionPolizaModel) == 0
    assert count(runtime, AuditoriaPolizaModel) == 1
