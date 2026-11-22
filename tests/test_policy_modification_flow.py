from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

from bff_movil.infrastructure.http_policy_modification_gateway import (
    HttpPolicyModificationGateway as MobileModificationGateway,
)
from bff_web.config import Config
from bff_web.domain.auth import User
from cryptography.hazmat.primitives.asymmetric import rsa
from polizas.infrastructure.models import AuditoriaPolizaModel, VersionPolizaModel
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from tests import test_policy_renewal_flow as renewal_flow
from tests.test_dashboard_flow import USER, sign_token
from tests.test_policy_renewal_flow import (
    INTERNAL_TOKEN,
    Runtime,
    headers,
    issue_policy,
    validate,
)

keypair = renewal_flow.keypair
runtime = renewal_flow.runtime

CHANGE = {"versionBase": 1, "plan": "premium", "coberturasOpcionales": []}


def test_emision_aceptada_se_consulta_y_modifica_sin_inventar_coberturas(
    runtime: Runtime, keypair: rsa.RSAPrivateKey
) -> None:
    emitted = runtime.policies.post(
        "/v1/polizas",
        json={
            "clienteId": str(USER.user_id),
            "suscripcionId": str(uuid4()),
            "pagoId": str(uuid4()),
            "tipo": "automotive",
            "confirmadaEn": datetime.now(UTC).isoformat(),
            "simulada": True,
            "fechasContratadas": None,
            "condicionesEmision": {
                "plan": "esencial",
                "prima": "41250",
                "coberturas": ["Daños al vehículo"],
            },
        },
        headers={"Authorization": f"Bearer {INTERNAL_TOKEN}"},
    )
    assert emitted.status_code == 200
    policy = emitted.get_json()
    policy_id = UUID(policy["id"])
    token = sign_token(keypair, scope="cliente")
    path = f"/v1/clientes/me/polizas/{policy_id}"
    runtime.clock.on(date.fromisoformat(policy["fechaInicio"]) + timedelta(days=1))
    config: Config = runtime.bff.application.extensions["bff_config"]
    mobile = MobileModificationGateway(config.policies_service_url, config.internal_api_token)

    initial = runtime.bff.get(path + "/modificacion", headers=headers(token))
    assert initial.status_code == 200
    initial_state = initial.get_json()
    validate("EstadoModificacionPoliza", initial_state)
    version = initial_state["versionActual"]
    assert version["prima"]["monto"] == "41250"
    liability = next(c for c in version["coberturas"] if c["codigo"] == "responsabilidad_civil")
    assert liability["incluida"] is False and liability["opcional"] is False
    assert (
        mobile.get_state(policy_id, USER.user_id, "consulta-emision").model_dump(
            mode="json", by_alias=True
        )
        == initial_state
    )

    simulation = runtime.bff.post(
        path + "/modificaciones/simulacion", json=CHANGE, headers=headers(token)
    )
    assert simulation.status_code == 200
    impact = simulation.get_json()
    validate("ImpactoModificacion", impact)
    assert impact["primaAnterior"]["monto"] == "41250"
    confirmed = runtime.bff.post(
        path + "/modificaciones",
        json={**CHANGE, "confirma": True, "impactoHash": impact["impactoHash"]},
        headers=headers(token, str(uuid4())),
    )
    assert confirmed.status_code == 201
    validate("ModificacionPoliza", confirmed.get_json())
    after = runtime.bff.get(path + "/modificacion", headers=headers(token))
    assert after.status_code == 200
    assert after.get_json()["historial"][1] == version
    assert (
        mobile.get_state(policy_id, USER.user_id, "consulta-endoso").model_dump(
            mode="json", by_alias=True
        )
        == after.get_json()
    )


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
