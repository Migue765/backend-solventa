from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from threading import Thread
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from bff_web.domain.auth import User
from cotizacion.application.pagos import ProcesarPagoSimulado
from cotizacion.domain.errors import ServicioPolizasNoDisponibleError
from cotizacion.infrastructure.persistence.models import PagoSimuladoModel, SagaEmisionModel
from cotizacion.infrastructure.persistence.repositorio_pagos import RepositorioPagosSqlAlchemy
from cotizacion.infrastructure.polizas_http import PolizasHttp
from cryptography.hazmat.primitives.asymmetric import rsa
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
    client, quotes, _ = policy_runtime
    gateway = quotes.application.extensions["polizas"]
    original = gateway.emitir

    def lose_response(evidence: dict[str, object], correlation_id: str) -> UUID:
        original(evidence, correlation_id)
        raise ServicioPolizasNoDisponibleError()

    monkeypatch.setattr(gateway, "emitir", lose_response)
    selection, _, failure = create_paid_request(client, token, "automotive", payment_status=503)
    assert failure["code"] == "servicio_no_disponible"
    policies = quotes.application.extensions["polizas_test_app"]
    with Session(policies.extensions["polizas_engine"]) as session:
        first_id: UUID = session.scalars(select(PolizaModel.poliza_id)).one()
    monkeypatch.setattr(gateway, "emitir", original)
    retry = client.post(
        f"/v1/solicitudes-cotizacion/{selection['solicitudId']}/pago-simulado",
        headers={**bearer(token), "Idempotency-Key": str(uuid4())},
        json={},
    )
    assert retry.status_code == 200
    dashboard = client.get("/v1/clientes/me/dashboard", headers=bearer(token)).get_json()
    assert [p["id"] for p in dashboard["polizas"]] == [str(first_id)]
