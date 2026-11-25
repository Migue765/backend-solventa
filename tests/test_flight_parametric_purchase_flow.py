"""Compra normal de dos clientes y cancelacion con reloj controlado solo en tests."""

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from threading import Thread
from typing import Self
from unittest.mock import Mock
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from bff_web.infrastructure.http_quote_gateway import HttpQuoteGateway
from cryptography.hazmat.primitives.asymmetric import rsa
from siniestros.app import create_app
from siniestros.domain import Config
from siniestros.infrastructure import pagos_parametricos, parametricos, vuelos
from siniestros.infrastructure.notificaciones import PublicadorNotificaciones
from siniestros.models import AutomaticPaymentCase, Claim, ParametricPayment, ParametricRule
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from werkzeug.serving import make_server

from tests import test_policies_flow as policy_flow
from tests.test_dashboard_flow import (
    INTERNAL_TOKEN,
    ROOT,
    USER,
    Runtime,
    bearer,
    create_paid_request,
    sign_token,
    validate_contract,
)

keypair = policy_flow.keypair
policy_runtime = policy_flow.policy_runtime
runtime = policy_flow.runtime
token = policy_flow.token

SOURCE_HEADERS = {"Authorization": "Bearer flight-source-test", "X-Fuente-ID": vuelos.FUENTE_VUELOS}


def test_purchase_flight_contract_drives_two_confirmed_indemnities_once(
    policy_runtime: Runtime,
    keypair: rsa.RSAPrivateKey,
    token: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, quotes, _ = policy_runtime
    second = replace(USER, user_id=uuid4(), email="otro@example.test")
    other_token = sign_token(keypair, second)
    _, subscription1, premium1 = create_paid_request(
        client,
        token,
        "parametric",
        offer_index=1,
    )
    _, subscription2, premium2 = create_paid_request(
        client,
        other_token,
        "parametric",
        offer_index=2,
    )
    create_paid_request(client, token, "parametric", offer_index=1, flight_number="AV999")
    create_paid_request(client, token, "parametric", offer_index=0)
    create_paid_request(
        client,
        token,
        "parametric",
        offer_index=2,
        coverage_type="missed-connection",
    )
    policies = quotes.application.extensions["polizas_test_app"]
    server = make_server("127.0.0.1", 0, policies, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"sqlite:///{tmp_path / 'flights.db'}"
    migrations = AlembicConfig(str(ROOT / "services" / "siniestros" / "alembic.ini"))
    migrations.set_main_option("sqlalchemy.url", url)
    command.upgrade(migrations, "head")
    claims = create_app(
        Config(
            url,
            tmp_path / "evidence",
            INTERNAL_TOKEN,
            f"http://127.0.0.1:{server.server_port}",
            parametric_sources={vuelos.FUENTE_VUELOS: "flight-source-test"},
            parametric_queue_capacity=1,
        )
    )
    occurred = datetime.now(UTC) + timedelta(days=30)

    class TestClock(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> Self:
            return cls.fromtimestamp((occurred + timedelta(minutes=1)).timestamp(), tz)

    monkeypatch.setattr(vuelos, "datetime", TestClock)
    monkeypatch.setattr(parametricos, "datetime", TestClock)
    monkeypatch.setattr(pagos_parametricos, "datetime", TestClock)
    event_id = str(uuid4())
    event = {
        "eventoId": event_id,
        "ocurridoEn": occurred.isoformat(),
        "tipoEvento": vuelos.TIPO_VUELO,
        "ubicacionCodigo": event_id,
        "metrica": {"nombre": "cancelacion", "valor": 1, "unidad": "boolean"},
        "vuelo": {
            "airline": "Test",
            "flightNumber": "AV123",
            "flightDate": occurred.date().isoformat(),
            "estado": "cancelado",
            "causa": "clima",
            "simulada": True,
        },
    }
    api = claims.test_client()
    claims_server = make_server("127.0.0.1", 0, claims, threaded=True)
    claims_thread = Thread(target=claims_server.serve_forever, daemon=True)
    claims_thread.start()
    client.application.extensions["quote_gateway"] = HttpQuoteGateway(
        client.application.extensions["bff_config"].quote_service_url,
        internal_api_token=INTERNAL_TOKEN,
        claims_url=f"http://127.0.0.1:{claims_server.server_port}",
    )
    client.application.extensions["bff_config"] = replace(
        client.application.extensions["bff_config"],
        claims_service_url=f"http://127.0.0.1:{claims_server.server_port}",
    )
    monkeypatch.setenv("OPENDATA_VUELOS_SIMULADO_TOKEN", "flight-source-test")
    internal = {"Authorization": f"Bearer {INTERNAL_TOKEN}"}
    try:
        public_event = {
            "eventoId": event["eventoId"],
            "ocurridoEn": event["ocurridoEn"],
            **event["vuelo"],
        }
        route = "/v1/backoffice/eventos-vuelo-simulados"
        assert client.post(route, json=public_event).status_code == 401
        assert client.post(route, headers=bearer(token), json=public_event).status_code == 403
        advisor = sign_token(
            keypair, replace(USER, role="asesor", partner_ids=("sintetico",)), scope="asesor"
        )
        future = deepcopy(event)
        future["ocurridoEn"] = (occurred + timedelta(minutes=2)).isoformat()
        assert (
            api.post(
                "/internal/v1/eventos-parametricos/recepcion",
                headers=SOURCE_HEADERS,
                json={"eventos": [future]},
            ).status_code
            == 422
        )
        response = client.post(route, headers=bearer(advisor), json=public_event)
        assert response.status_code == 202, response.get_json()
        conflict = deepcopy(event)
        conflict["vuelo"]["flightNumber"] = "AV999"
        rejected = api.post(
            "/internal/v1/eventos-parametricos/recepcion",
            headers=SOURCE_HEADERS,
            json={"eventos": [conflict]},
        )
        assert rejected.status_code == 409
        overloaded = deepcopy(conflict)
        overloaded["eventoId"] = overloaded["ubicacionCodigo"] = str(uuid4())
        assert (
            api.post(
                "/internal/v1/eventos-parametricos/recepcion",
                headers=SOURCE_HEADERS,
                json={"eventos": [overloaded]},
            ).status_code
            == 429
        )
        with Session(claims.extensions["siniestros_engine"]) as session:
            assert session.scalar(select(func.count()).select_from(ParametricRule)) == 2
        processed = api.post(
            "/internal/v1/eventos-parametricos/procesamiento", headers=internal, json={"limite": 10}
        )
        assert processed.get_json()["casosValidos"] == 2
        pending = api.get(
            "/v1/indemnizaciones", headers={**internal, "X-Cliente-ID": str(USER.user_id)}
        ).get_json()
        assert len(pending["indemnizaciones"]) == 1
        assert next(iter(pending["indemnizaciones"].values()))["monto"] is None
        paid = api.post(
            "/internal/v1/pagos-parametricos/procesamiento", headers=internal, json={"limite": 10}
        )
        assert paid.get_json()["confirmados"] == 2, paid.get_json()
        for owner, amount in ((USER.user_id, "1725000.00"), (second.user_id, "1950000.00")):
            indemnities = api.get(
                "/v1/indemnizaciones",
                headers={**internal, "X-Cliente-ID": str(owner)},
            ).get_json()["indemnizaciones"]
            assert len(indemnities) == 1
            result = next(iter(indemnities.values()))
            assert result["estado"] == "pagada" and result["monto"] == amount
            assert result["referencia"] and result["pagadaEn"] and result["simulada"] is True
        replay = api.post(
            "/internal/v1/eventos-parametricos/recepcion",
            headers=SOURCE_HEADERS,
            json={"eventos": [event]},
        )
        assert replay.get_json()["duplicados"] == 1
        api.post(
            "/internal/v1/eventos-parametricos/procesamiento", headers=internal, json={"limite": 10}
        )
        assert (
            api.post(
                "/internal/v1/pagos-parametricos/procesamiento",
                headers=internal,
                json={"limite": 10},
            ).get_json()["confirmados"]
            == 0
        )
        reissued = deepcopy(event)
        reissued["eventoId"] = reissued["ubicacionCodigo"] = str(uuid4())
        assert (
            api.post(
                "/internal/v1/eventos-parametricos/recepcion",
                headers=SOURCE_HEADERS,
                json={"eventos": [reissued]},
            ).status_code
            == 202
        )
        api.post(
            "/internal/v1/eventos-parametricos/procesamiento", headers=internal, json={"limite": 10}
        )
        assert (
            api.post(
                "/internal/v1/pagos-parametricos/procesamiento",
                headers=internal,
                json={"limite": 10},
            ).get_json()["confirmados"]
            == 0
        )
        with Session(claims.extensions["siniestros_engine"]) as session:
            for model in (Claim, AutomaticPaymentCase, ParametricPayment):
                assert session.scalar(select(func.count()).select_from(model)) == 2
        sender = Mock()
        sender.enviar.return_value = True
        publisher = PublicadorNotificaciones(
            claims.extensions["siniestros_repository"].sessions, sender
        )
        assert publisher.publicar(100)["fallidas"] == 0
        confirmations = [
            call.args[0]["indemnizacion"]
            for call in sender.enviar.call_args_list
            if call.args[0]["tipo"] == "pago_parametrico_ejecutado"
        ]
        assert sorted(item["monto"] for item in confirmations) == ["1725000.00", "1950000.00"]
        assert all(item["referencia"] and item["simulada"] is True for item in confirmations)
        assert publisher.publicar(100) == {"publicadas": 0, "fallidas": 0}
        dashboard = client.get("/v1/clientes/me/dashboard", headers=bearer(token)).get_json()
        validate_contract(dashboard)
        paid_policy = next(
            p for p in dashboard["polizas"] if p["suscripcionId"] == subscription1["id"]
        )
        assert paid_policy["indemnizacion"]["monto"] == "1725000.00"
        assert paid_policy["estado"] == "programada"
        assert {p["suscripcionId"] for p in dashboard["polizas"]} >= {subscription1["id"]}
        assert subscription2["id"] not in {p["suscripcionId"] for p in dashboard["polizas"]}
        for premium, indemnity in ((premium1, "1725000.00"), (premium2, "1950000.00")):
            premium_amount = premium["importe"]
            assert isinstance(premium_amount, dict)
            assert premium_amount["monto"] != indemnity
        unauthenticated = api.get("/v1/indemnizaciones")
        assert unauthenticated.status_code == 401
    finally:
        claims_server.shutdown()
        claims_thread.join(timeout=5)
        claims_server.server_close()
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        claims.extensions["siniestros_engine"].dispose()
