from datetime import date, timedelta
from uuid import uuid4

from bff_web.domain.auth import User
from cryptography.hazmat.primitives.asymmetric import rsa
from polizas.infrastructure.models import AuditoriaPolizaModel, CancelacionModel, PolizaModel
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from tests import test_policy_renewal_flow as renewal_flow
from tests.test_dashboard_flow import sign_token
from tests.test_policy_renewal_flow import Runtime, headers, issue_policy, validate

keypair = renewal_flow.keypair
runtime = renewal_flow.runtime


def count(runtime: Runtime, model: type[object]) -> int:
    with Session(create_engine(runtime.database_url)) as session:
        return int(session.scalar(select(func.count()).select_from(model)) or 0)


def test_cliente_consulta_confirma_y_queda_cancelada(
    runtime: Runtime, keypair: rsa.RSAPrivateKey
) -> None:
    token = sign_token(keypair, scope="cliente")
    policy = issue_policy(runtime)
    runtime.clock.on(date.fromisoformat(policy["fechaInicio"]) + timedelta(days=10))
    path = f"/v1/clientes/me/polizas/{policy['id']}"

    state = runtime.bff.get(path + "/cancelacion", headers=headers(token))
    assert state.status_code == 200
    assert state.headers["Cache-Control"] == "no-store"
    validate("EstadoCancelacionPoliza", state.get_json())
    settlement = state.get_json()["liquidacion"]
    assert state.get_json()["cancelable"] is True
    assert settlement["valorPendiente"]["monto"] == "0"
    assert settlement["coberturasFinalizadas"]

    body = {
        "confirma": True,
        "motivoCancelacion": "costo",
        "liquidacionHash": settlement["liquidacionHash"],
    }
    validate("ConfirmacionCancelacion", body)
    unconfirmed = runtime.bff.post(
        path + "/cancelaciones",
        json={**body, "confirma": False},
        headers=headers(token, str(uuid4())),
    )
    assert unconfirmed.status_code == 400

    key = str(uuid4())
    cancelled = runtime.bff.post(path + "/cancelaciones", json=body, headers=headers(token, key))
    assert cancelled.status_code == 201
    validate("CancelacionPoliza", cancelled.get_json())
    assert cancelled.get_json()["liquidacion"] == settlement
    replay = runtime.bff.post(path + "/cancelaciones", json=body, headers=headers(token, key))
    assert replay.status_code == 200 and replay.get_json() == cancelled.get_json()

    again = runtime.bff.post(
        path + "/cancelaciones", json=body, headers=headers(token, str(uuid4()))
    )
    assert again.status_code == 422 and again.get_json()["motivo"] == "poliza_cancelada"
    validate("Problema", again.get_json())

    after = runtime.bff.get(path + "/cancelacion", headers=headers(token)).get_json()
    validate("EstadoCancelacionPoliza", after)
    assert after["estado"] == "cancelada" and after["cancelacion"] == cancelled.get_json()

    renewal = runtime.bff.get(path + "/renovacion", headers=headers(token)).get_json()
    validate("ElegibilidadRenovacion", renewal)
    assert renewal["motivo"] == "poliza_cancelada"
    modification = runtime.bff.get(path + "/modificacion", headers=headers(token)).get_json()
    validate("EstadoModificacionPoliza", modification)
    assert modification["motivo"] == "poliza_cancelada"

    assert count(runtime, CancelacionModel) == 1
    with Session(create_engine(runtime.database_url)) as session:
        assert session.scalar(select(PolizaModel.estado)) == "cancelada"
        audit: list[tuple[str, str | None]] = [
            (row.resultado, row.motivo)
            for row in session.execute(
                select(AuditoriaPolizaModel.resultado, AuditoriaPolizaModel.motivo).where(
                    AuditoriaPolizaModel.accion == "cancelacion"
                )
            ).all()
        ]
    assert sorted(audit) == [
        ("confirmada", "costo"),
        ("rechazada", "poliza_cancelada"),
    ]


def test_rechazos_se_publican_como_problemas(runtime: Runtime, keypair: rsa.RSAPrivateKey) -> None:
    token = sign_token(keypair, scope="cliente")
    policy = issue_policy(runtime)
    runtime.clock.on(date.fromisoformat(policy["fechaInicio"]) + timedelta(days=2))
    path = f"/v1/clientes/me/polizas/{policy['id']}/cancelaciones"
    body = {"confirma": True, "motivoCancelacion": "otro", "liquidacionHash": "a" * 64}

    stale = runtime.bff.post(path, json=body, headers=headers(token, str(uuid4())))
    assert stale.status_code == 409 and stale.get_json()["code"] == "liquidacion_cambio"
    validate("Problema", stale.get_json())

    other = sign_token(
        keypair, User(uuid4(), "Otra", "otra@solventa.co", "cliente"), scope="cliente"
    )
    foreign = runtime.bff.post(path, json=body, headers=headers(other, str(uuid4())))
    assert foreign.status_code == 404

    runtime.clock.on(date.fromisoformat(policy["fechaFin"]))
    ending = runtime.bff.get(
        f"/v1/clientes/me/polizas/{policy['id']}/cancelacion", headers=headers(token)
    ).get_json()
    validate("EstadoCancelacionPoliza", ending)
    assert ending["cancelable"] is False and ending["motivo"] == "fin_de_vigencia"

    assert count(runtime, CancelacionModel) == 0
