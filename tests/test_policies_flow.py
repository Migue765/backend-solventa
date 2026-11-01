from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from threading import Thread
from uuid import UUID, uuid4

import pytest
import yaml
from alembic import command
from alembic.config import Config
from bff_web.domain.auth import User
from cotizacion.application.pagos import ProcesarPagoSimulado
from cotizacion.domain.errors import ServicioPolizasNoDisponibleError
from cotizacion.infrastructure.pago_simulado import SimuladorPago
from cotizacion.infrastructure.persistence.models import (
    IntentoPagoSimuladoModel,
    OutboxEventoModel,
    PagoSimuladoModel,
    SagaEmisionModel,
)
from cotizacion.infrastructure.persistence.repositorio_pagos import RepositorioPagosSqlAlchemy
from cotizacion.infrastructure.polizas_http import PolizasHttp
from cryptography.hazmat.primitives.asymmetric import rsa
from openapi_schema_validator import OAS31Validator
from polizas.app import create_app
from polizas.infrastructure.models import PolizaModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from werkzeug.serving import make_server

from tests import test_dashboard_flow as dashboard_flow
from tests.test_dashboard_flow import (
    INTERNAL_TOKEN,
    ROOT,
    Runtime,
    bearer,
    create_paid_request,
    sign_token,
    validate_contract,
)

keypair = dashboard_flow.keypair
runtime = dashboard_flow.runtime
token = dashboard_flow.token


@pytest.fixture
def policy_runtime(runtime: Runtime, tmp_path: Path) -> Iterator[Runtime]:
    url = f"sqlite:///{tmp_path / 'polizas.db'}"
    config = Config(str(ROOT / "services" / "polizas" / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    policies = create_app(url, INTERNAL_TOKEN)
    server = make_server("127.0.0.1", 0, policies)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _, quotes, factory = runtime
    quotes.application.extensions["polizas"] = PolizasHttp(
        f"http://127.0.0.1:{server.server_port}", INTERNAL_TOKEN
    )
    repository = RepositorioPagosSqlAlchemy(factory, emitir_polizas=True)
    quotes.application.extensions["repositorio_pagos"] = repository
    quotes.application.extensions["procesar_pago_simulado"] = ProcesarPagoSimulado(repository)
    quotes.application.extensions["polizas_test_app"] = policies
    try:
        yield runtime
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        policies.extensions["polizas_engine"].dispose()


@pytest.mark.parametrize(
    "product",
    [
        "automotive",
        "life",
        "device-protection",
        "payment-protection",
        "mortgage-life",
        "travel",
        "parametric",
        "parametric-weather",
    ],
)
def test_payment_emits_persisted_policy_visible_only_to_owner(
    policy_runtime: Runtime, token: str, keypair: rsa.RSAPrivateKey, product: str
) -> None:
    client, quotes, factory = policy_runtime
    selection, subscription, payment = create_paid_request(client, token, product)
    dashboard = client.get("/v1/clientes/me/dashboard", headers=bearer(token))
    assert dashboard.status_code == 200, dashboard.get_json()
    body = dashboard.get_json()
    validate_contract(body)
    assert len(body["polizas"]) == 1
    policy = body["polizas"][0]
    assert policy["suscripcionId"] == subscription["id"]
    assert policy["tipo"] == product
    assert policy["simulada"] is True
    if product not in {"travel", "parametric", "parametric-weather"}:
        assert policy["fechaInicio"] == datetime.now(UTC).date().isoformat()
        assert policy["estado"] == "vigente"
    else:
        start = date.today() + timedelta(days=30)
        assert policy["fechaInicio"] == start.isoformat()
        end = start if product == "parametric" else start + timedelta(days=7)
        assert policy["fechaFin"] == end.isoformat()
        assert policy["estado"] == "programada"
    retry = client.post(
        f"/v1/solicitudes-cotizacion/{selection['solicitudId']}/pago-simulado",
        headers={**bearer(token), "Idempotency-Key": str(uuid4())},
        json={},
    )
    assert retry.status_code == 200
    assert retry.get_json() == payment
    assert client.get("/v1/clientes/me/dashboard", headers=bearer(token)).get_json()["polizas"] == [
        policy
    ]
    other_token = sign_token(keypair, User(uuid4(), "Otro", "otro@solventa.co", "cliente"))
    assert (
        client.get("/v1/clientes/me/dashboard", headers=bearer(other_token)).get_json()["polizas"]
        == []
    )
    assert client.get("/v1/clientes/me/dashboard").status_code == 401
    assert (
        client.get(
            "/v1/clientes/me/dashboard?clienteId=" + str(uuid4()), headers=bearer(token)
        ).status_code
        == 400
    )
    with factory() as session:
        saga = session.get(SagaEmisionModel, UUID(str(subscription["id"])))
        assert saga is not None
        assert (saga.estado, saga.version) == ("emitida", 3)
        assert session.scalar(select(func.count()).select_from(PagoSimuladoModel)) == 1
    policies = quotes.application.extensions["polizas_test_app"]
    with Session(policies.extensions["polizas_engine"]) as session:
        stored = session.get(PolizaModel, UUID(policy["id"]))
        assert stored is not None
        assert stored.suscripcion_id == UUID(str(subscription["id"]))
        assert stored.pago_id == UUID(str(payment["id"]))
        assert session.scalar(select(func.count()).select_from(PolizaModel)) == 1


def test_historical_payment_never_emits_automatically(policy_runtime: Runtime, token: str) -> None:
    client, quotes, factory = policy_runtime
    historical = RepositorioPagosSqlAlchemy(factory, emitir_polizas=False)
    quotes.application.extensions["procesar_pago_simulado"] = ProcesarPagoSimulado(historical)
    selection, _, _ = create_paid_request(client, token, "automotive")
    migration = Config(str(ROOT / "services" / "cotizacion" / "alembic.ini"))
    migration.set_main_option(
        "sqlalchemy.url", quotes.application.extensions["cotizacion_config"].database_url
    )
    command.downgrade(migration, "pagos_simulados")
    command.upgrade(migration, "head")
    enabled = quotes.application.extensions["repositorio_pagos"]
    quotes.application.extensions["procesar_pago_simulado"] = ProcesarPagoSimulado(enabled)
    response = client.post(
        f"/v1/solicitudes-cotizacion/{selection['solicitudId']}/pago-simulado",
        headers={**bearer(token), "Idempotency-Key": str(uuid4())},
        json={},
    )
    assert response.status_code == 200
    assert (
        client.get("/v1/clientes/me/dashboard", headers=bearer(token)).get_json()["polizas"] == []
    )


def test_emission_failure_keeps_payment_and_retry_emits_once(
    policy_runtime: Runtime, token: str
) -> None:
    client, quotes, factory = policy_runtime
    gateway = quotes.application.extensions["polizas"]
    original = gateway.url
    gateway.url = "http://127.0.0.1:1"
    _, _, failure = create_paid_request(client, token, "automotive", payment_status=503)
    assert failure["code"] == "servicio_no_disponible"
    with factory() as session:
        paid: PagoSimuladoModel = session.scalars(select(PagoSimuladoModel)).one()
        request_id = paid.solicitud_id
        payment_id = paid.pago_id
        assert paid.emision_solicitada is True
        saga = session.get(SagaEmisionModel, paid.suscripcion_id)
        assert saga is not None and saga.estado == "pago_confirmado"
    assert client.get("/v1/clientes/me/dashboard", headers=bearer(token)).status_code == 503
    gateway.url = original
    assert (
        client.get("/v1/clientes/me/dashboard", headers=bearer(token)).get_json()["polizas"] == []
    )
    retry = client.post(
        f"/v1/solicitudes-cotizacion/{request_id}/pago-simulado",
        headers={**bearer(token), "Idempotency-Key": str(uuid4())},
        json={},
    )
    assert retry.status_code == 200
    assert retry.get_json()["id"] == str(payment_id)
    assert (
        len(client.get("/v1/clientes/me/dashboard", headers=bearer(token)).get_json()["polizas"])
        == 1
    )


def test_lost_emission_acknowledgement_retries_without_duplicate_policy(
    policy_runtime: Runtime, token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, quotes, factory = policy_runtime
    gateway = quotes.application.extensions["polizas"]
    policies = quotes.application.extensions["polizas_test_app"]
    original = gateway.emitir

    def lose_response(evidence: dict[str, object], correlation_id: str) -> UUID:
        with factory() as session:
            paid: PagoSimuladoModel = session.scalars(select(PagoSimuladoModel)).one()
            assert str(paid.pago_id) == evidence["pagoId"]
            saga = session.get(SagaEmisionModel, paid.suscripcion_id)
            assert saga is not None and (saga.estado, saga.version) == ("pago_confirmado", 2)
            event: OutboxEventoModel = session.scalars(
                select(OutboxEventoModel).where(OutboxEventoModel.tipo == "PagoSimuladoConfirmado")
            ).one()
            assert event.payload["pagoId"] == evidence["pagoId"]
        original(evidence, correlation_id)
        raise ServicioPolizasNoDisponibleError()

    monkeypatch.setattr(gateway, "emitir", lose_response)
    selection, started, failure = create_paid_request(
        client, token, "automotive", payment_status=503
    )
    assert failure["code"] == "servicio_no_disponible"
    base = f"/v1/solicitudes-cotizacion/{selection['solicitudId']}"
    headers = bearer(token)
    payment_response = client.get(base + "/pago-simulado", headers=headers)
    assert payment_response.status_code == 200
    payment = payment_response.get_json()
    assert payment["solicitudId"] == selection["solicitudId"]
    assert payment["suscripcionId"] == started["id"]
    assert payment["estado"] == "confirmado"
    assert payment["simulada"] is True
    assert payment["saga"] == {
        "estado": "pago_confirmado",
        "version": 2,
        "actualizadaEn": payment["confirmadaEn"],
    }
    subscription_response = client.get(base + "/suscripcion", headers=headers)
    assert subscription_response.status_code == 200
    subscription = subscription_response.get_json()
    assert subscription["id"] == started["id"]
    assert subscription["saga"] == payment["saga"]
    dashboard_response = client.get("/v1/clientes/me/dashboard", headers=headers)
    assert dashboard_response.status_code == 200
    dashboard = dashboard_response.get_json()
    validate_contract(dashboard)
    assert len(dashboard["polizas"]) == 1
    policy = dashboard["polizas"][0]
    assert policy["suscripcionId"] == started["id"]
    assert dashboard["suscripciones"][0]["id"] == started["id"]
    assert dashboard["suscripciones"][0]["saga"] == payment["saga"]
    with Session(policies.extensions["polizas_engine"]) as session:
        stored: PolizaModel = session.scalars(select(PolizaModel)).one()
        first_id = stored.poliza_id
        assert policy["id"] == str(first_id)
        assert stored.pago_id == UUID(payment["id"])
        assert stored.suscripcion_id == UUID(str(started["id"]))
    with factory() as session:
        paid: PagoSimuladoModel = session.scalars(select(PagoSimuladoModel)).one()
        assert str(paid.pago_id) == payment["id"]
        idempotency_key = paid.idempotency_key
        saga = session.get(SagaEmisionModel, paid.suscripcion_id)
        assert saga is not None and (saga.estado, saga.version) == ("pago_confirmado", 2)
        assert (
            session.scalar(
                select(func.count())
                .select_from(OutboxEventoModel)
                .where(OutboxEventoModel.tipo == "PolizaAcademicaEmitida")
            )
            == 0
        )

    monkeypatch.setattr(gateway, "emitir", original)
    for _ in range(2):
        retry = client.post(
            base + "/pago-simulado",
            headers={**headers, "Idempotency-Key": str(idempotency_key)},
            json={},
        )
        assert retry.status_code == 200
        assert retry.get_json() == payment
        current_response = client.get(base + "/suscripcion", headers=headers)
        assert current_response.status_code == 200
        current = current_response.get_json()
        assert current["id"] == started["id"]
        assert current["saga"]["estado"] == "emitida"
        assert current["saga"]["version"] == 3
        snapshot_response = client.get(base + "/pago-simulado", headers=headers)
        assert snapshot_response.status_code == 200
        assert snapshot_response.get_json() == payment
    recovered_response = client.get("/v1/clientes/me/dashboard", headers=headers)
    assert recovered_response.status_code == 200
    recovered = recovered_response.get_json()
    validate_contract(recovered)
    assert recovered["polizas"] == [policy]
    assert recovered["suscripciones"][0]["saga"] == current["saga"]
    with factory() as session:
        paid = session.scalars(select(PagoSimuladoModel)).one()
        assert str(paid.pago_id) == payment["id"]
        for event_type, id_field, identifier in (
            ("PagoSimuladoConfirmado", "pagoId", payment["id"]),
            ("PolizaAcademicaEmitida", "polizaId", str(first_id)),
        ):
            event: OutboxEventoModel = session.scalars(
                select(OutboxEventoModel).where(OutboxEventoModel.tipo == event_type)
            ).one()
            assert event.agregado_id == paid.suscripcion_id
            assert event.payload[id_field] == identifier
    with Session(policies.extensions["polizas_engine"]) as session:
        assert session.scalars(select(PolizaModel.poliza_id)).one() == first_id


def test_rejected_payment_is_visible_only_to_owner_and_never_emits(
    policy_runtime: Runtime,
    token: str,
    keypair: rsa.RSAPrivateKey,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, quotes, factory = policy_runtime
    repository = quotes.application.extensions["repositorio_pagos"]
    monkeypatch.setattr(repository, "_proveedor", SimuladorPago("rechazado"))
    gateway = quotes.application.extensions["polizas"]
    original = gateway.emitir

    def emission_forbidden(*_: object) -> UUID:
        raise AssertionError("Un rechazo nunca debe invocar al emisor")

    monkeypatch.setattr(gateway, "emitir", emission_forbidden)
    selection, subscription, rejection = create_paid_request(client, token, "automotive")
    base = f"/v1/solicitudes-cotizacion/{selection['solicitudId']}"
    headers = bearer(token)
    assert rejection["estado"] == "rechazado"
    assert rejection["simulada"] is True
    assert rejection["motivo"] == "rechazo_simulado"
    assert "confirmadaEn" not in rejection
    assert rejection["saga"] == subscription["saga"]
    contract = yaml.safe_load(
        (ROOT / "contracts" / "openapi" / "bff-web-v1.yaml").read_text(encoding="utf-8")
    )
    payment_schema = {
        "$ref": "#/components/schemas/PagoSimulado",
        "components": contract["components"],
    }
    validator = OAS31Validator(payment_schema, format_checker=OAS31Validator.FORMAT_CHECKER)
    validator.validate(rejection)
    assert not validator.is_valid({**rejection, "confirmadaEn": rejection["rechazadaEn"]})
    assert not validator.is_valid({**rejection, "estado": "confirmado"})
    assert not validator.is_valid({**rejection, "saga": {"estado": "emitida", "version": 3}})
    retrieved = client.get(base + "/pago-simulado", headers=headers)
    assert retrieved.status_code == 200 and retrieved.get_json() == rejection
    assert client.get(base + "/suscripcion", headers=headers).get_json() == subscription
    dashboard_response = client.get("/v1/clientes/me/dashboard", headers=headers)
    assert dashboard_response.status_code == 200
    dashboard = dashboard_response.get_json()
    validate_contract(dashboard)
    assert dashboard["polizas"] == []
    assert dashboard["pagos"] == [
        {
            key: rejection[key]
            for key in (
                "id",
                "solicitudId",
                "suscripcionId",
                "estado",
                "simulada",
                "importe",
                "periodicidad",
                "rechazadaEn",
                "motivo",
            )
        }
    ]
    policies = quotes.application.extensions["polizas_test_app"]
    with Session(policies.extensions["polizas_engine"]) as session:
        assert session.scalar(select(func.count()).select_from(PolizaModel)) == 0
    with factory() as session:
        attempt: IntentoPagoSimuladoModel = session.scalars(select(IntentoPagoSimuladoModel)).one()
        key = str(attempt.idempotency_key)
        assert str(attempt.intento_id) == rejection["id"]
        assert session.scalar(select(func.count()).select_from(PagoSimuladoModel)) == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(OutboxEventoModel)
                .where(
                    OutboxEventoModel.tipo.in_(["PagoSimuladoConfirmado", "PolizaAcademicaEmitida"])
                )
            )
            == 0
        )
    other = bearer(sign_token(keypair, User(uuid4(), "Otro", "otro@solventa.co", "cliente")))
    assert client.get(base + "/pago-simulado", headers=other).status_code == 404
    assert (
        client.post(
            base + "/pago-simulado", headers={**other, "Idempotency-Key": key}, json={}
        ).status_code
        == 404
    )
    assert client.get("/v1/clientes/me/dashboard", headers=other).get_json()["pagos"] == []
    second_rejection = client.post(
        base + "/pago-simulado", headers={**headers, "Idempotency-Key": str(uuid4())}, json={}
    )
    assert second_rejection.status_code == 200
    latest = second_rejection.get_json()
    validator.validate(latest)
    assert latest["estado"] == "rechazado" and latest["id"] != rejection["id"]
    assert client.get(base + "/pago-simulado", headers=headers).get_json() == latest
    latest_dashboard = client.get("/v1/clientes/me/dashboard", headers=headers).get_json()
    validate_contract(latest_dashboard)
    assert len(latest_dashboard["pagos"]) == 1
    assert latest_dashboard["pagos"][0]["id"] == latest["id"]
    assert latest_dashboard["polizas"] == []
    monkeypatch.setattr(repository, "_proveedor", SimuladorPago("confirmado"))
    replay = client.post(
        base + "/pago-simulado", headers={**headers, "Idempotency-Key": key}, json={}
    )
    assert replay.status_code == 200 and replay.get_json() == rejection
    assert client.get(base + "/pago-simulado", headers=headers).get_json() == latest
    assert client.get("/v1/clientes/me/dashboard", headers=headers).get_json() == latest_dashboard
    monkeypatch.setattr(gateway, "emitir", original)
    approved = client.post(
        base + "/pago-simulado", headers={**headers, "Idempotency-Key": str(uuid4())}, json={}
    )
    assert approved.status_code == 200 and approved.get_json()["estado"] == "confirmado"
    validator.validate(approved.get_json())
    current = client.get("/v1/clientes/me/dashboard", headers=headers).get_json()
    validate_contract(current)
    assert len(current["polizas"]) == 1
    assert len(current["pagos"]) == 1 and current["pagos"][0]["estado"] == "confirmado"
    assert current["suscripciones"][0]["saga"]["estado"] == "emitida"
    another_key = client.post(
        base + "/pago-simulado", headers={**headers, "Idempotency-Key": str(uuid4())}, json={}
    )
    assert another_key.status_code == 200 and another_key.get_json() == approved.get_json()
    assert (
        client.post(
            base + "/pago-simulado", headers={**headers, "Idempotency-Key": key}, json={}
        ).get_json()
        == rejection
    )
    after_replay = client.get("/v1/clientes/me/dashboard", headers=headers).get_json()
    assert after_replay == current
    assert client.get(base + "/pago-simulado", headers=headers).get_json() == approved.get_json()
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(IntentoPagoSimuladoModel)) == 3
        assert session.scalar(select(func.count()).select_from(PagoSimuladoModel)) == 1
        for event_type, expected in (
            ("PagoSimuladoRechazado", 2),
            ("PagoSimuladoConfirmado", 1),
            ("PolizaAcademicaEmitida", 1),
        ):
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(OutboxEventoModel)
                    .where(OutboxEventoModel.tipo == event_type)
                )
                == expected
            )
    with Session(policies.extensions["polizas_engine"]) as session:
        assert session.scalar(select(func.count()).select_from(PolizaModel)) == 1
