import json
import math
import os
import re
import time
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any, Self
from uuid import uuid4

import pytest
from cotizacion.infrastructure.persistence.admision_socios import AdmisionSocios
from cotizacion.infrastructure.persistence.database import crear_sesiones
from cotizacion.infrastructure.persistence.models import (
    AdmisionSocioModel,
    CotizacionModel,
    OfertaHipotecariaModel,
    OperacionSocioModel,
    ReservaOperacionModel,
    SeleccionOfertaModel,
)
from sqlalchemy import func, select

from tests import test_profiling_flow
from tests.test_profiling_flow import CONSENT, Flow

flow = test_profiling_flow.flow

PATH = "/v1/socios/solicitudes-cotizacion"
CONTEXT = "/v1/socios/actual"


def test_portal_preflight_contract_is_owned_by_gateway_not_bff() -> None:
    terraform = (
        Path(__file__).resolve().parents[1] / "terraform" / "modules" / "api-gateway" / "main.tf"
    ).read_text(encoding="utf-8")
    api = terraform.split('resource "aws_apigatewayv2_api" "this" {', 1)[1].split("\n}", 1)[0]
    cors = api.split("cors_configuration {", 1)[1].split("}", 1)[0]
    for setting, required in (
        ("allow_methods", {"GET", "OPTIONS"}),
        ("allow_headers", {"authorization", "api-version", "x-correlation-id"}),
        ("expose_headers", {"api-version", "x-correlation-id", "retry-after"}),
    ):
        match = re.search(rf"\b{setting}\s*=\s*(\[[^\]]*\])", cors)
        assert match is not None
        assert required.issubset(set(json.loads(match.group(1))))
    assert "allow_origins     = var.cors_allowed_origins" in cors
    protected = terraform.split('resource "aws_apigatewayv2_route" "v1" {', 1)[1]
    assert 'authorization_type = "JWT"' in protected.split("\n}", 1)[0]


def partner_headers(flow: Flow, partner_id: str, email: str) -> dict[str, str]:
    created = flow.identity.test_cli_runner().invoke(
        args=[
            "create-partner-user",
            "--name",
            "Socio integrado",
            "--email",
            email,
            "--partner-id",
            partner_id,
            "--partner-name",
            "Banco sintetico",
        ],
        input="Solventa123!\nSolventa123!\n",
    )
    assert created.exit_code == 0, created.output
    login = flow.client.post(
        "/v1/sesiones",
        json={
            "correo": email,
            "contrasena": "Solventa123!",
        },
    )
    assert login.status_code == 200
    assert login.get_json()["usuario"]["rol"] == "socio"
    assert login.get_json()["usuario"]["socios"] == [partner_id]
    return {"Authorization": "Bearer " + login.get_json()["accessToken"]}


def prepare(flow: Flow) -> tuple[dict[str, str], dict]:
    headers = partner_headers(flow, "BANK-E2E", "socio@integracion.co")
    flow.authorize()
    body = {
        "operationId": str(uuid4()),
        "socioId": "BANK-E2E",
        "productoId": "mortgage-life",
        "datosProducto": {"mortgageBalance": "100000000"},
        "cliente": {
            "tipoCliente": "persona_natural",
            "tipoDocumento": "cc",
            "numeroDocumento": "1012345678",
        },
    }
    return headers, body


def partner_state(flow: Flow) -> dict[str, object]:
    with crear_sesiones(flow.quote_database)() as session:
        return {
            "operations": session.scalar(select(func.count()).select_from(OperacionSocioModel)),
            "quotes": session.scalar(select(func.count()).select_from(CotizacionModel)),
            "reservations": session.scalar(select(func.count()).select_from(ReservaOperacionModel)),
            "selections": session.scalar(select(func.count()).select_from(SeleccionOfertaModel)),
            "quotas": {
                row.socio_id: row.usadas for row in session.scalars(select(AdmisionSocioModel))
            },
        }


def test_portal_context_matches_authenticated_session_without_cross_partner_leaks(
    flow: Flow,
) -> None:
    headers_a = partner_headers(flow, "BANK-A", "portal-a@integracion.co")
    headers_b = partner_headers(flow, "BANK-B", "portal-b@integracion.co")
    original = partner_state(flow)
    contexts: dict[str, dict[str, Any]] = {}
    for headers, partner, foreign in (
        (headers_a, "BANK-A", "BANK-B"),
        (headers_b, "BANK-B", "BANK-A"),
        (headers_a, "BANK-A", "BANK-B"),
    ):
        session = flow.client.get("/v1/sesiones/actual", headers=headers).get_json()
        correlation = str(uuid4())
        response = flow.client.get(
            CONTEXT + "?socioId=" + foreign,
            headers={**headers, "API-Version": "1", "X-Correlation-ID": correlation},
        )
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["API-Version"] == "1"
        assert response.headers["X-Correlation-ID"] == correlation
        context = response.get_json()
        assert set(context) == {"apiVersion", "usuario", "socios", "alcances"}
        assert context["apiVersion"] == "1"
        assert context["usuario"] == session["usuario"]
        assert context["usuario"]["rol"] == "socio"
        assert context["usuario"]["socios"] == [partner]
        assert all(
            isinstance(context["usuario"][key], str) and context["usuario"][key]
            for key in ("id", "nombre", "correo")
        )
        assert context["alcances"] == ["perfilamientos:crear", "clientes:consultar"]
        assert len(context["socios"]) == 1
        assert set(context["socios"][0]) == {"socioId", "productos"}
        assert context["socios"][0]["socioId"] == partner
        products = context["socios"][0]["productos"]
        assert products == ["mortgage-life"]
        assert all(isinstance(product, str) and product for product in products)
        assert foreign not in response.get_data(as_text=True)
        assert partner_state(flow) == original
        if partner in contexts:
            assert context == contexts[partner]
        contexts[partner] = context
    assert contexts["BANK-A"]["usuario"]["id"] != contexts["BANK-B"]["usuario"]["id"]


def test_portal_context_rejects_invalid_access_with_correlated_sanitized_audit(
    flow: Flow, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    headers = partner_headers(flow, "BANK-PORTAL", "portal@integracion.co")
    original = partner_state(flow)
    future = datetime.now(UTC) + timedelta(hours=2)

    class ExpiredClock(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> Self:
            return cls.fromtimestamp(future.timestamp(), tz)

    attempts: tuple[tuple[dict[str, str], int, str, bool], ...] = (
        ({}, 401, "sesion_invalida", False),
        ({"Authorization": "Bearer invalid-portal-credential"}, 401, "sesion_invalida", False),
        (flow.headers, 403, "alcance_insuficiente", False),
        ({**headers, "API-Version": "2"}, 400, "version_no_soportada", False),
        (headers, 401, "sesion_invalida", True),
    )
    for credentials, status, code, expired in attempts:
        caplog.clear()
        correlation = str(uuid4())
        with monkeypatch.context() as patcher:
            if expired:
                patcher.setattr("jwt.api_jwt.datetime", ExpiredClock)
            response = flow.client.get(
                CONTEXT,
                headers={**credentials, "X-Correlation-ID": correlation},
            )
        assert response.status_code == status
        assert response.get_json()["code"] == code
        assert response.headers["X-Correlation-ID"] == correlation
        if status == 401:
            assert response.headers["WWW-Authenticate"] == "Bearer"
        assert not {"usuario", "socios", "alcances"}.intersection(response.get_json())
        records = [record for record in caplog.records if getattr(record, "event", None)]
        assert len(records) == 1
        event = json.loads(records[0].getMessage())
        assert event["code"] == code
        assert event["status"] == status
        assert event["correlationId"] == correlation
        assert "socioId" not in event
        if status != 400:
            assert "actorId" not in event
        for sensitive in (
            headers["Authorization"].removeprefix("Bearer "),
            flow.headers["Authorization"].removeprefix("Bearer "),
            "invalid-portal-credential",
            "portal@integracion.co",
            "Solventa123!",
        ):
            assert sensitive not in records[0].getMessage()
        assert partner_state(flow) == original
    assert flow.client.get(CONTEXT, headers=headers).status_code == 200


def test_partner_login_consent_profile_quote_persistence_and_recovery(flow: Flow) -> None:
    headers, body = prepare(flow)
    context = flow.client.get("/v1/socios/actual", headers=headers)
    assert context.status_code == 200
    print("partner_context=" + json.dumps(context.get_json()))
    assert context.get_json()["socios"][0]["productos"] == ["mortgage-life"]
    post_headers = {**headers, "Idempotency-Key": body["operationId"], "API-Version": "1"}
    first = flow.client.post(PATH, json=body, headers=post_headers)
    assert first.status_code == 201, first.get_json()
    offer = first.get_json()["oferta"]
    assert [factor["nombre"] for factor in offer["explicacion"]["factoresDeterminantes"]] == [
        "Saldo hipotecario",
        "Perfil de riesgo autorizado",
        "Regla y plan",
    ]
    assert offer["trazabilidad"]["reglaVersion"] == "academico-v1"
    assert {source["nombre"] for source in offer["trazabilidad"]["fuentes"]} == {
        "open_finance",
        "open_data",
    }
    retry = flow.client.post(PATH, json=body, headers=post_headers)
    assert retry.get_json() == first.get_json()
    recovered = flow.client.get(f"{PATH}/{body['operationId']}?socioId=BANK-E2E", headers=headers)
    assert recovered.status_code == 200 and recovered.get_json() == first.get_json()
    with crear_sesiones(flow.quote_database)() as session:
        assert session.scalar(select(func.count()).select_from(CotizacionModel)) == 1
        assert session.scalar(select(func.count()).select_from(OperacionSocioModel)) == 1
        assert session.scalar(select(func.count()).select_from(OfertaHipotecariaModel)) == 1
    request_id = first.get_json()["solicitudId"]
    customer_view = flow.client.get(
        f"/v1/solicitudes-cotizacion/{request_id}", headers=flow.headers
    )
    assert customer_view.status_code == 200
    assert customer_view.get_json()["productoId"] == "mortgage-life"
    consent = flow.client.get(CONSENT, headers=flow.headers).get_json()
    revoked = flow.client.put(
        CONSENT,
        json={"autorizado": False, "version": consent["version"]},
        headers=flow.headers,
    )
    assert revoked.status_code == 200
    new_body = {**body, "operationId": str(uuid4())}
    denied = flow.client.post(
        PATH, json=new_body, headers={**headers, "Idempotency-Key": new_body["operationId"]}
    )
    assert denied.status_code == 403
    assert denied.get_json()["code"] == "consentimiento_no_vigente"
    with crear_sesiones(flow.quote_database)() as session:
        assert session.scalar(select(func.count()).select_from(CotizacionModel)) == 1


def test_two_partners_cannot_recover_or_operate_foreign_ids(flow: Flow) -> None:
    headers_a, body = prepare(flow)
    headers_b = partner_headers(flow, "BANK-B", "socio-b@integracion.co")
    limits = AdmisionSocios(crear_sesiones(flow.quote_database))
    limits.configurar("BANK-E2E", 1, 1)
    limits.configurar("BANK-B", 1, 1)
    body_b = {**body, "socioId": "BANK-B"}
    first = flow.client.post(
        PATH, json=body_b, headers={**headers_b, "Idempotency-Key": body_b["operationId"]}
    )
    assert first.status_code == 201
    operation = first.get_json()
    request_path = "/v1/solicitudes-cotizacion/" + operation["solicitudId"]
    customer_request = flow.client.get(request_path, headers=flow.headers)
    assert customer_request.status_code == 200
    customer_offers = flow.client.get(request_path + "/ofertas", headers=flow.headers)
    assert customer_offers.status_code == 200
    offer = customer_offers.get_json()["ofertas"][0]
    original = partner_state(flow)
    assert original == {
        "operations": 1,
        "quotes": 1,
        "reservations": 1,
        "selections": 0,
        "quotas": {"BANK-E2E": 0, "BANK-B": 1},
    }
    attempts = [
        ("GET", f"{PATH}/{body_b['operationId']}?socioId=BANK-B", None, 403),
        ("GET", f"{PATH}/{body_b['operationId']}?socioId=BANK-E2E", None, 404),
        ("POST", PATH, body_b, 403),
        ("POST", PATH, {**body_b, "socioId": "BANK-E2E"}, 409),
        ("GET", request_path, None, 404),
        ("GET", request_path + "/ofertas", None, 404),
        (
            "PUT",
            request_path + "/seleccion",
            {"ofertaId": offer["id"], "version": offer["version"]},
            404,
        ),
    ]
    for method, path, payload, status in attempts:
        rejected = flow.client.open(
            path,
            method=method,
            json=payload,
            headers={**headers_a, "Idempotency-Key": body_b["operationId"]},
        )
        assert rejected.status_code == status, (method, path, rejected.get_json())
        for key in ("clienteId", "solicitudId", "cotizacionId", "perfilamientoReferencia"):
            assert operation[key] not in rejected.get_data(as_text=True)
        assert partner_state(flow) == original
    recovered = flow.client.get(f"{PATH}/{body_b['operationId']}?socioId=BANK-B", headers=headers_b)
    assert recovered.status_code == 200 and recovered.get_json() == operation
    replay = flow.client.post(
        PATH, json=body_b, headers={**headers_b, "Idempotency-Key": body_b["operationId"]}
    )
    assert replay.status_code == 201 and replay.get_json() == operation
    assert partner_state(flow) == original
    new_a = {**body, "operationId": str(uuid4())}
    assert (
        flow.client.post(
            PATH, json=new_a, headers={**headers_a, "Idempotency-Key": new_a["operationId"]}
        ).status_code
        == 201
    )
    assert partner_state(flow)["quotas"] == {"BANK-E2E": 1, "BANK-B": 1}


def test_exhausted_partner_does_not_consume_other_partners_quota(flow: Flow) -> None:
    headers_a, body = prepare(flow)
    headers_b = partner_headers(flow, "BANK-B", "socio-b@integracion.co")
    limits = AdmisionSocios(crear_sesiones(flow.quote_database))
    limits.configurar("BANK-E2E", 1, 1)
    limits.configurar("BANK-B", 1, 1)
    first = flow.client.post(
        PATH, json=body, headers={**headers_a, "Idempotency-Key": body["operationId"]}
    )
    assert first.status_code == 201
    original = partner_state(flow)
    blocked_a = {**body, "operationId": str(uuid4())}
    denied = flow.client.post(
        PATH, json=blocked_a, headers={**headers_a, "Idempotency-Key": blocked_a["operationId"]}
    )
    assert denied.status_code == 429
    assert denied.get_json()["code"] == "cuota_socio_excedida"
    assert 1 <= int(denied.headers["Retry-After"]) <= 60
    assert partner_state(flow) == original
    context = flow.client.get(CONTEXT, headers={**headers_a, "API-Version": "1"})
    assert context.status_code == 200
    assert context.get_json()["usuario"]["socios"] == ["BANK-E2E"]
    assert partner_state(flow) == original
    body_b = {**body, "operationId": str(uuid4()), "socioId": "BANK-B"}
    second = flow.client.post(
        PATH, json=body_b, headers={**headers_b, "Idempotency-Key": body_b["operationId"]}
    )
    assert second.status_code == 201
    completed = partner_state(flow)
    assert completed["quotas"] == {"BANK-E2E": 1, "BANK-B": 1}
    assert completed["operations"] == completed["quotes"] == 2
    for headers, payload, expected in (
        (headers_a, body, first.get_json()),
        (headers_b, body_b, second.get_json()),
    ):
        recovered = flow.client.get(
            f"{PATH}/{payload['operationId']}?socioId={payload['socioId']}", headers=headers
        )
        assert recovered.status_code == 200 and recovered.get_json() == expected
        replay = flow.client.post(
            PATH, json=payload, headers={**headers, "Idempotency-Key": payload["operationId"]}
        )
        assert replay.status_code == 201 and replay.get_json() == expected
        assert partner_state(flow) == completed


def test_admission_audit_correlation_crosses_http_without_changing_replay(
    flow: Flow, caplog: pytest.LogCaptureFixture
) -> None:
    headers, body = prepare(flow)
    AdmisionSocios(crear_sesiones(flow.quote_database)).configurar("BANK-E2E", 1, 2)
    post_headers = {**headers, "Idempotency-Key": body["operationId"]}
    first = flow.client.post(PATH, json=body, headers=post_headers)
    assert first.status_code == 201
    assert flow.client.post(PATH, json=body, headers=post_headers).get_json() == first.get_json()
    assert not [record for record in caplog.records if getattr(record, "event", None)]
    correlation_id = str(uuid4())
    denied_body = {**body, "operationId": str(uuid4())}
    response = flow.client.post(
        PATH,
        json=denied_body,
        headers={
            **headers,
            "Idempotency-Key": denied_body["operationId"],
            "X-Correlation-ID": correlation_id,
        },
    )
    assert response.status_code == 429
    assert response.get_json()["code"] == "cuota_socio_excedida"
    assert response.headers["X-Correlation-ID"] == correlation_id
    assert 1 <= int(response.headers["Retry-After"]) <= 60
    records = [record for record in caplog.records if getattr(record, "event", None)]
    assert len(records) == 2
    assert {record.name for record in records} == {"bff_web.app", "cotizacion.app"}
    for record in records:
        event = json.loads(record.getMessage())
        assert event["event"] == "sol325.rechazo"
        assert event["code"] == "cuota_socio_excedida"
        assert event["correlationId"] == correlation_id
        assert event["status"] == 429
        assert headers["Authorization"].removeprefix("Bearer ") not in record.getMessage()
        assert body["cliente"]["numeroDocumento"] not in record.getMessage()
        assert "+573001234567" not in record.getMessage()
    with crear_sesiones(flow.quote_database)() as session:
        assert session.scalar(select(func.count()).select_from(CotizacionModel)) == 1
        assert session.scalar(select(func.count()).select_from(OperacionSocioModel)) == 1


@pytest.mark.skipif(
    os.getenv("RUN_PARTNER_BENCHMARK") != "1", reason="Ensayo de latencia explicito"
)
def test_partner_journey_latency_100_requests(flow: Flow) -> None:
    headers, body = prepare(flow)
    AdmisionSocios(crear_sesiones(flow.quote_database)).configurar("BANK-E2E", 200, 2)
    samples = []
    for _ in range(100):
        body["operationId"] = str(uuid4())
        started = time.perf_counter()
        response = flow.client.post(
            PATH, json=body, headers={**headers, "Idempotency-Key": body["operationId"]}
        )
        samples.append((time.perf_counter() - started) * 1000)
        assert response.status_code == 201, response.get_json()
    samples.sort()
    p95, p99 = (
        samples[math.ceil(len(samples) * 0.95) - 1],
        samples[math.ceil(len(samples) * 0.99) - 1],
    )
    print(
        "local Windows, HTTP downstream, SQLite, mortgage-life "
        f"n=100 p95={p95:.2f}ms p99={p99:.2f}ms"
    )
    assert p95 <= 250
    assert p99 <= 500
