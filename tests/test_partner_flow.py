import json
import math
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any, Self
from uuid import uuid4

import pytest
import yaml
from cotizacion.domain.pago import ResultadoSimuladorPago
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
from openapi_schema_validator import OAS31Validator
from sqlalchemy import func, select

from tests import test_profiling_flow
from tests.test_profiling_flow import CONSENT, Flow

flow = test_profiling_flow.flow

PATH = "/v1/socios/solicitudes-cotizacion"
CONTEXT = "/v1/socios/actual"
QUOTE_CONTRACT = yaml.safe_load(
    (Path(__file__).resolve().parents[1] / "contracts" / "openapi" / "bff-web-v1.yaml").read_text(
        encoding="utf-8"
    )
)


def validate_history(payload: dict, schema: str) -> None:
    OAS31Validator(
        {"$ref": f"#/components/schemas/{schema}", "components": QUOTE_CONTRACT["components"]},
        format_checker=OAS31Validator.FORMAT_CHECKER,
    ).validate(payload)


def test_portal_preflight_contract_is_owned_by_gateway_not_bff() -> None:
    terraform = (
        Path(__file__).resolve().parents[1] / "terraform" / "modules" / "api-gateway" / "main.tf"
    ).read_text(encoding="utf-8")
    api = terraform.split('resource "aws_apigatewayv2_api" "this" {', 1)[1].split("\n}", 1)[0]
    cors = api.split("cors_configuration {", 1)[1].split("}", 1)[0]
    for setting, required in (
        ("allow_methods", {"GET", "PUT", "OPTIONS"}),
        (
            "allow_headers",
            {"authorization", "content-type", "api-version", "x-correlation-id", "upload-offset"},
        ),
    ):
        match = re.search(rf"\b{setting}\s*=\s*(\[[^\]]*\])", cors)
        assert match is not None
        assert required.issubset(set(json.loads(match.group(1))))
    assert re.search(r"\bexpose_headers\s*=\s*var\.cors_expose_headers\b", cors)
    variables = (
        Path(__file__).resolve().parents[1]
        / "terraform"
        / "modules"
        / "api-gateway"
        / "variables.tf"
    ).read_text(encoding="utf-8")
    exposed = variables.split('variable "cors_expose_headers" {', 1)[1].split("\n}", 1)[0]
    default = re.search(r"\bdefault\s*=\s*(\[[^\]]*\])", exposed)
    assert default is not None
    assert {"api-version", "x-correlation-id", "retry-after", "location"}.issubset(
        set(json.loads(default.group(1)))
    )
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
        "datosProducto": {
            "mortgageBalance": "100000000",
            "referenciaCredito": "HIP-E2E-001",
        },
        "cliente": {
            "tipoCliente": "persona_natural",
            "tipoDocumento": "cc",
            "numeroDocumento": "1012345678",
        },
    }
    return headers, body


def test_partner_explicit_offer_selection_is_persisted_and_does_not_accept_or_pay(
    flow: Flow,
) -> None:
    headers, body = prepare(flow)
    operation = flow.client.post(
        PATH, json=body, headers={**headers, "Idempotency-Key": body["operationId"]}
    )
    assert operation.status_code == 201, operation.get_json()
    operation_path = f"{PATH}/{body['operationId']}?socioId=BANK-E2E"
    offer_path = f"{PATH}/{body['operationId']}/oferta?socioId=BANK-E2E"
    selection_path = f"{PATH}/{body['operationId']}/seleccion-provisional?socioId=BANK-E2E"
    assert flow.client.get(offer_path, headers=headers).status_code == 404
    assert flow.client.get(selection_path, headers=headers).status_code == 404
    calculated = flow.client.post(offer_path, json={}, headers=headers)
    assert calculated.status_code == 201, calculated.get_json()
    snapshot = calculated.get_json()
    assert flow.client.get(offer_path, headers=headers).get_json() == snapshot
    assert len(snapshot["ofertas"]) == 3
    assert flow.client.get(selection_path, headers=headers).get_json() == {
        "solicitudId": operation.get_json()["solicitudId"],
        "seleccion": None,
        "aceptacion": None,
        "estadoOferta": None,
    }
    chosen = next(item for item in snapshot["ofertas"] if not item["recomendada"])
    choice = {
        "ofertaId": chosen["ofertaId"],
        "version": chosen["version"],
        "revisionEsperada": 0,
    }
    key = str(uuid4())
    attempt = {**headers, "Idempotency-Key": key}
    for invalid in (
        {**choice, "ofertaId": str(uuid4())},
        {**choice, "version": choice["version"] + 1},
        {**choice, "ofertaId": snapshot["ofertaId"]},
    ):
        rejected = flow.client.put(selection_path, json=invalid, headers=attempt)
        assert rejected.status_code == 422, rejected.get_json()
    selected = flow.client.put(selection_path, json=choice, headers=attempt)
    assert selected.status_code == 200, selected.get_json()
    assert selected.get_json()["ofertaId"] == chosen["ofertaId"]
    assert selected.get_json()["revision"] == 1
    assert selected.get_json()["prima"] == chosen["prima"]
    assert flow.client.put(selection_path, json=choice, headers=attempt).get_json() == (
        selected.get_json()
    )
    state = flow.client.get(selection_path, headers=headers).get_json()
    assert state["seleccion"] == selected.get_json()
    assert state["aceptacion"] is None
    assert state["estadoOferta"] == "disponible"
    assert flow.client.get(operation_path, headers=headers).get_json()["estadoSeleccion"] == state
    other_offer = next(
        item for item in snapshot["ofertas"] if item["ofertaId"] != choice["ofertaId"]
    )
    replacement = {
        "ofertaId": other_offer["ofertaId"],
        "version": other_offer["version"],
        "revisionEsperada": 1,
    }
    conflict = flow.client.put(selection_path, json=replacement, headers=attempt)
    assert conflict.status_code == 409
    assert conflict.get_json()["code"] == "conflicto_idempotencia"
    stale = flow.client.put(
        selection_path, json=choice, headers={**headers, "Idempotency-Key": str(uuid4())}
    )
    assert stale.status_code == 409
    assert stale.get_json()["code"] == "seleccion_desactualizada"
    changed = flow.client.put(
        selection_path, json=replacement, headers={**headers, "Idempotency-Key": str(uuid4())}
    )
    assert changed.status_code == 200, changed.get_json()
    assert changed.get_json()["revision"] == 2
    assert flow.client.put(selection_path, json=choice, headers=attempt).get_json() == (
        selected.get_json()
    )
    assert flow.client.get(selection_path, headers=headers).get_json()["seleccion"] == (
        changed.get_json()
    )
    with crear_sesiones(flow.quote_database)() as session:
        assert session.scalar(select(func.count()).select_from(CotizacionModel)) == 1
        assert session.scalar(select(func.count()).select_from(SeleccionOfertaModel)) == 0
    foreign = partner_headers(flow, "OTHER", "other-selection@integracion.co")
    for method, data in (("GET", None), ("PUT", replacement)):
        for unauthorized in (foreign, flow.headers):
            denied = flow.client.open(
                selection_path,
                method=method,
                json=data,
                headers={**unauthorized, "Idempotency-Key": str(uuid4())},
            )
            assert denied.status_code == 403
    assert (
        flow.client.get(selection_path.replace("BANK-E2E", "OTHER"), headers=foreign).status_code
        == 404
    )
    consent = flow.client.get(CONSENT, headers=flow.headers).get_json()
    revoked = flow.client.put(
        CONSENT, json={"autorizado": False, "version": consent["version"]}, headers=flow.headers
    )
    assert revoked.status_code == 200
    for path in (offer_path, selection_path, operation_path):
        denied = flow.client.get(path, headers=headers)
        assert denied.status_code == 403, denied.get_json()
        assert denied.get_json()["code"] == "consentimiento_invalido"
    denied = flow.client.put(selection_path, json=replacement, headers=attempt)
    assert denied.status_code == 403
    flow.authorize(version=revoked.get_json()["version"])
    assert flow.client.get(offer_path, headers=headers).status_code == 403
    assert flow.client.get(selection_path, headers=headers).status_code == 403


def test_partner_selection_is_compatible_with_client_acceptance_without_partner_payment(
    flow: Flow,
) -> None:
    headers, body = prepare(flow)
    operation = flow.client.post(
        PATH, json=body, headers={**headers, "Idempotency-Key": body["operationId"]}
    )
    assert operation.status_code == 201
    snapshot_path = f"{PATH}/{body['operationId']}/oferta?socioId=BANK-E2E"
    snapshot = flow.client.post(snapshot_path, json={}, headers=headers).get_json()
    chosen = snapshot["ofertas"][1]
    path = f"{PATH}/{body['operationId']}/seleccion-provisional?socioId=BANK-E2E"
    choice = {"ofertaId": chosen["ofertaId"], "version": chosen["version"], "revisionEsperada": 0}
    selected = flow.client.put(
        path, json=choice, headers={**headers, "Idempotency-Key": str(uuid4())}
    )
    assert selected.status_code == 200, selected.get_json()
    request_path = f"/v1/solicitudes-cotizacion/{snapshot['solicitudId']}"
    accepted = flow.client.put(
        request_path + "/seleccion",
        json={"ofertaId": chosen["ofertaId"], "version": chosen["version"], "revisionSeleccion": 1},
        headers={**flow.headers, "Idempotency-Key": str(uuid4())},
    )
    assert accepted.status_code == 200, accepted.get_json()
    state = flow.client.get(path, headers=headers)
    assert state.status_code == 200, state.get_json()
    assert state.get_json()["estadoOferta"] == "aceptada"
    assert state.get_json()["aceptacion"]["ofertaId"] == chosen["ofertaId"]
    terminal = flow.client.put(
        path,
        json={**choice, "revisionEsperada": 1},
        headers={**headers, "Idempotency-Key": str(uuid4())},
    )
    assert terminal.status_code == 409, terminal.get_json()
    assert terminal.get_json()["code"] == "seleccion_no_modificable"


def test_customer_profile_and_birth_date_use_identity_registration_over_http(flow: Flow) -> None:
    flow.authorize()
    profile = flow.client.get("/v1/clientes/me/perfil", headers=flow.headers)
    assert profile.status_code == 200
    assert profile.get_json()["fechaNacimiento"] == "1990-01-01"
    created = flow.client.post(
        "/v1/solicitudes-cotizacion",
        json={"productoId": "life", "datosProducto": {}},
        headers={**flow.headers, "Idempotency-Key": str(uuid4())},
    )
    assert created.status_code == 201, created.get_json()
    assert created.get_json()["datosProducto"]["birthDate"] == "1990-01-01"
    forged = flow.client.post(
        "/v1/solicitudes-cotizacion",
        json={"productoId": "life", "datosProducto": {"birthDate": "2000-01-01"}},
        headers={**flow.headers, "Idempotency-Key": str(uuid4())},
    )
    assert forged.status_code == 400
    with crear_sesiones(flow.quote_database)() as session:
        assert session.scalar(select(func.count()).select_from(CotizacionModel)) == 1


def test_partner_client_list_uses_registered_quotes_and_current_consent_over_http(
    flow: Flow,
) -> None:
    headers, body = prepare(flow)
    foreign = partner_headers(flow, "OTHER", "other-list@integracion.co")
    path = "/v1/socios/clientes?socioId=BANK-E2E&productoId=mortgage-life"
    assert flow.client.get(path, headers=headers).get_json() == {"clientes": []}
    flow.create_quote()
    assert flow.client.get(path, headers=headers).get_json() == {"clientes": []}
    for operation in (body["operationId"], str(uuid4())):
        result = flow.client.post(
            PATH,
            json={**body, "operationId": operation},
            headers={**headers, "Idempotency-Key": operation},
        )
        assert result.status_code == 201, result.get_json()
        client_id = result.get_json()["clienteId"]
    with crear_sesiones(flow.quote_database)() as session:
        assert session.scalar(select(func.count()).select_from(OperacionSocioModel)) == 2
        assert session.scalar(select(func.count()).select_from(CotizacionModel)) == 3
    listed = flow.client.get(path, headers=headers)
    assert listed.status_code == 200
    assert listed.get_json() == {
        "clientes": [
            {
                "clienteId": client_id,
                "nombre": "Cliente integrado",
                "tipoDocumento": "cc",
                "numeroDocumentoEnmascarado": "******5678",
                "consentimientoEstado": "vigente",
            }
        ]
    }
    assert flow.client.get(path, headers=foreign).status_code == 403
    assert flow.client.get(path.replace("BANK-E2E", "OTHER"), headers=foreign).get_json() == {
        "clientes": []
    }
    assert flow.client.get(path, headers=flow.headers).status_code == 403
    consent = flow.client.get(CONSENT, headers=flow.headers).get_json()
    assert (
        flow.client.put(
            CONSENT,
            json={"autorizado": False, "version": consent["version"]},
            headers=flow.headers,
        ).status_code
        == 200
    )
    redacted = flow.client.get(path, headers=headers)
    assert redacted.status_code == 200
    assert redacted.get_json() == {
        "clientes": [
            {
                "clienteId": client_id,
                "nombre": None,
                "tipoDocumento": None,
                "numeroDocumentoEnmascarado": None,
                "consentimientoEstado": "revocado",
            }
        ]
    }


def selected_partner_request(flow: Flow, headers: dict[str, str], body: dict) -> tuple[dict, dict]:
    created = flow.client.post(
        PATH, json=body, headers={**headers, "Idempotency-Key": body["operationId"]}
    )
    assert created.status_code == 201, created.get_json()
    base = f"{PATH}/{body['operationId']}"
    calculated = flow.client.post(base + "/oferta?socioId=BANK-E2E", json={}, headers=headers)
    assert calculated.status_code == 201, calculated.get_json()
    chosen = calculated.get_json()["ofertas"][1]
    selected = flow.client.put(
        base + "/seleccion-provisional?socioId=BANK-E2E",
        json={"ofertaId": chosen["ofertaId"], "version": chosen["version"], "revisionEsperada": 0},
        headers={**headers, "Idempotency-Key": str(uuid4())},
    )
    assert selected.status_code == 200, selected.get_json()
    return created.get_json(), chosen


def request_history(flow: Flow, request_id: str) -> dict:
    response = flow.client.get(
        f"/v1/solicitudes-cotizacion/{request_id}/estado", headers=flow.headers
    )
    assert response.status_code == 200, response.get_json()
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["API-Version"] == "1"
    payload = response.get_json()
    assert isinstance(payload, dict)
    validate_history(payload, "ResumenSolicitudCliente")
    return payload


def test_history_lists_all_registered_requests_without_customer_dedup_or_dashboard_limit(
    flow: Flow,
) -> None:
    headers, body = prepare(flow)
    history_path = PATH + "?socioId=BANK-E2E"
    assert flow.client.get(history_path, headers=headers).get_json() == {"solicitudes": []}
    assert flow.client.get("/v1/solicitudes-cotizacion", headers=flow.headers).get_json() == {
        "solicitudes": []
    }
    AdmisionSocios(crear_sesiones(flow.quote_database)).configurar("BANK-E2E", 11, 11)
    operations = []
    for _ in range(11):
        candidate = {**body, "operationId": str(uuid4())}
        response = flow.client.post(
            PATH, json=candidate, headers={**headers, "Idempotency-Key": candidate["operationId"]}
        )
        assert response.status_code == 201, response.get_json()
        operations.append(response.get_json())
    denied = {**body, "operationId": str(uuid4())}
    assert (
        flow.client.post(
            PATH, json=denied, headers={**headers, "Idempotency-Key": denied["operationId"]}
        ).status_code
        == 429
    )
    foreign = partner_headers(flow, "OTHER", "history-other@integracion.co")
    another = {**body, "operationId": str(uuid4()), "socioId": "OTHER"}
    response = flow.client.post(
        PATH, json=another, headers={**foreign, "Idempotency-Key": another["operationId"]}
    )
    assert response.status_code == 201, response.get_json()
    direct_id = flow.create_quote()
    history = flow.client.get(history_path, headers=headers)
    assert history.status_code == 200, history.get_json()
    rows = history.get_json()["solicitudes"]
    for row in rows:
        validate_history(row, "ResumenSolicitudSocio")
    assert len(rows) == 11
    assert len({row["clienteId"] for row in rows}) == 1
    assert {row["operationId"] for row in rows} == {row["operationId"] for row in operations}
    assert all(row["estado"] == "pendiente" and row["socioId"] == "BANK-E2E" for row in rows)
    assert [row["creadaEn"] for row in rows] == sorted(
        (row["creadaEn"] for row in rows), reverse=True
    )
    assert (
        flow.client.get(history_path + "&productoId=mortgage-life", headers=headers).get_json()
        == history.get_json()
    )
    for query, credentials, status in (
        ("?socioId=BANK-E2E", foreign, 403),
        ("?socioId=BANK-E2E", flow.headers, 403),
        ("?socioId=BANK-E2E&productoId=life", headers, 400),
        ("?socioId=BANK-E2E&productoId=", headers, 400),
        ("?socioId=BANK-E2E&socioId=OTHER", headers, 400),
    ):
        assert flow.client.get(PATH + query, headers=credentials).status_code == status
    own = flow.client.get("/v1/solicitudes-cotizacion", headers=flow.headers).get_json()[
        "solicitudes"
    ]
    assert len(own) == 13
    assert any(row["solicitudId"] == direct_id and row["operationId"] is None for row in own)
    assert all(
        not {"consentimientoId", "consentimientoVersion", "cliente"}.intersection(row)
        for row in own
    )
    assert request_history(flow, rows[0]["solicitudId"]) == next(
        row for row in own if row["solicitudId"] == rows[0]["solicitudId"]
    )
    registered = flow.identity.test_client().post(
        "/v1/usuarios",
        json={
            "nombre": "Otro cliente",
            "correo": "history-client@integracion.co",
            "contrasena": "Solventa123!",
            "tipoDocumento": "cc",
            "numeroDocumento": "1012345679",
            "telefono": "+573001234568",
            "fechaNacimiento": "1991-01-01",
        },
    )
    assert registered.status_code == 201
    login = flow.client.post(
        "/v1/sesiones",
        json={"correo": "history-client@integracion.co", "contrasena": "Solventa123!"},
    )
    stranger = {"Authorization": "Bearer " + login.get_json()["accessToken"]}
    assert flow.client.get("/v1/solicitudes-cotizacion", headers=stranger).get_json() == {
        "solicitudes": []
    }
    base = f"/v1/solicitudes-cotizacion/{rows[0]['solicitudId']}"
    assert flow.client.get(base + "/estado", headers=stranger).status_code == 404
    assert (
        flow.client.put(
            base + "/rechazo", json={}, headers={**stranger, "Idempotency-Key": str(uuid4())}
        ).status_code
        == 404
    )
    assert len(flow.client.get(history_path, headers=headers).get_json()["solicitudes"]) == 11


def test_client_quote_rejection_is_durable_idempotent_and_blocks_acceptance(
    flow: Flow,
) -> None:
    headers, body = prepare(flow)
    operation, chosen = selected_partner_request(flow, headers, body)
    base = f"/v1/solicitudes-cotizacion/{operation['solicitudId']}"
    assert request_history(flow, operation["solicitudId"])["estado"] == "pendiente"
    assert flow.client.get(base + "/rechazo", headers=flow.headers).status_code == 404
    assert (
        flow.client.put(
            base + "/rechazo", json={}, headers={**headers, "Idempotency-Key": str(uuid4())}
        ).status_code
        == 403
    )
    key = str(uuid4())
    attempt = {**flow.headers, "Idempotency-Key": key}
    rejected = flow.client.put(base + "/rechazo", json={}, headers=attempt)
    assert rejected.status_code == 200, rejected.get_json()
    validate_history(rejected.get_json(), "RechazoSolicitud")
    assert (
        flow.client.put(base + "/rechazo", json={}, headers=attempt).get_json()
        == rejected.get_json()
    )
    assert (
        flow.client.get(base + "/rechazo", headers=flow.headers).get_json() == rejected.get_json()
    )
    from cotizacion.infrastructure.persistence.repositorio_historial_solicitudes import (
        RepositorioHistorialSolicitudesSql,
    )

    flow.quotes.extensions["historial_solicitudes"] = RepositorioHistorialSolicitudesSql(
        crear_sesiones(flow.quote_database)
    )
    assert (
        flow.client.get(base + "/rechazo", headers=flow.headers).get_json() == rejected.get_json()
    )
    current = request_history(flow, operation["solicitudId"])
    assert current["estado"] == "rechazada"
    assert current["rechazadaEn"] == rejected.get_json()["rechazadaEn"]
    assert current["pagoEstado"] is None and current["completadaEn"] is None
    assert current["estadoSeleccion"]["estadoOferta"] == "retirada"
    denied = flow.client.put(
        base + "/seleccion",
        json={"ofertaId": chosen["ofertaId"], "version": chosen["version"], "revisionSeleccion": 1},
        headers={**flow.headers, "Idempotency-Key": str(uuid4())},
    )
    assert denied.status_code == 409, denied.get_json()
    second = {**body, "operationId": str(uuid4())}
    another, _ = selected_partner_request(flow, headers, second)
    conflict = flow.client.put(
        f"/v1/solicitudes-cotizacion/{another['solicitudId']}/rechazo", json={}, headers=attempt
    )
    assert conflict.status_code == 409, conflict.get_json()
    assert conflict.get_json()["code"] == "conflicto_idempotencia"
    assert request_history(flow, another["solicitudId"])["estado"] == "pendiente"
    assert (
        flow.client.put(
            f"{PATH}/{body['operationId']}/seleccion-provisional?socioId=BANK-E2E",
            json={
                "ofertaId": chosen["ofertaId"],
                "version": chosen["version"],
                "revisionEsperada": 1,
            },
            headers={**headers, "Idempotency-Key": str(uuid4())},
        ).status_code
        == 409
    )


def test_quote_rejection_and_client_acceptance_are_mutually_exclusive_in_race(
    flow: Flow,
) -> None:
    headers, body = prepare(flow)
    operation, chosen = selected_partner_request(flow, headers, body)
    base = f"/v1/solicitudes-cotizacion/{operation['solicitudId']}"

    def decide(action: str) -> int:
        with flow.client.application.test_client() as client:
            response = client.put(
                base + "/" + action,
                json={}
                if action == "rechazo"
                else {
                    "ofertaId": chosen["ofertaId"],
                    "version": chosen["version"],
                    "revisionSeleccion": 1,
                },
                headers={**flow.headers, "Idempotency-Key": str(uuid4())},
            )
            return response.status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(decide, ("rechazo", "seleccion")))
    assert sorted(outcomes) == [200, 409]
    state = request_history(flow, operation["solicitudId"])
    assert (state["estado"] == "rechazada") != (state["estadoSeleccion"]["aceptacion"] is not None)
    assert state["pagoEstado"] is None


@pytest.mark.parametrize("payment_result", ["confirmado", "rechazado"])
def test_history_completed_requires_client_payment_and_is_not_client_rejection(
    flow: Flow, monkeypatch: pytest.MonkeyPatch, payment_result: str
) -> None:
    headers, body = prepare(flow)
    operation, chosen = selected_partner_request(flow, headers, body)
    request_id = operation["solicitudId"]
    base = f"/v1/solicitudes-cotizacion/{request_id}"
    assert request_history(flow, request_id)["estado"] == "pendiente"
    for method, suffix, payload in (
        (
            "PUT",
            "/seleccion",
            {"ofertaId": chosen["ofertaId"], "version": chosen["version"], "revisionSeleccion": 1},
        ),
        ("POST", "/suscripcion", {}),
        ("POST", "/pago-simulado", {}),
    ):
        assert (
            flow.client.open(
                base + suffix,
                method=method,
                json=payload,
                headers={**headers, "Idempotency-Key": str(uuid4())},
            ).status_code
            == 403
        )
    accepted = flow.client.put(
        base + "/seleccion",
        json={"ofertaId": chosen["ofertaId"], "version": chosen["version"], "revisionSeleccion": 1},
        headers={**flow.headers, "Idempotency-Key": str(uuid4())},
    )
    assert accepted.status_code == 200, accepted.get_json()
    assert request_history(flow, request_id)["estado"] == "pendiente"
    assert (
        flow.client.put(
            base + "/rechazo", json={}, headers={**flow.headers, "Idempotency-Key": str(uuid4())}
        ).status_code
        == 409
    )
    subscription = flow.client.post(
        base + "/suscripcion", headers={**flow.headers, "Idempotency-Key": str(uuid4())}
    )
    assert subscription.status_code == 201, subscription.get_json()
    assert request_history(flow, request_id)["estado"] == "pendiente"
    provider = flow.quotes.extensions["procesar_pago_simulado"]._proveedor
    monkeypatch.setattr(provider, "_resultado", ResultadoSimuladorPago(payment_result))
    key = str(uuid4())
    payment = flow.client.post(
        base + "/pago-simulado", json={}, headers={**flow.headers, "Idempotency-Key": key}
    )
    assert payment.status_code == 200, payment.get_json()
    state = request_history(flow, request_id)
    expected = "completada" if payment_result == "confirmado" else "pendiente"
    assert state["estado"] == expected
    assert state["pagoEstado"] == payment_result
    assert state["rechazadaEn"] is None
    if payment_result == "confirmado":
        assert state["completadaEn"] is not None
    expired = datetime.fromisoformat(state["validaHasta"]) + timedelta(seconds=1)
    monkeypatch.setattr(flow.quotes.extensions["historial_solicitudes"], "_reloj", lambda: expired)
    assert request_history(flow, request_id)["estado"] == (
        "completada" if payment_result == "confirmado" else "vencida"
    )
    consent = flow.client.get(CONSENT, headers=flow.headers).get_json()
    assert (
        flow.client.put(
            CONSENT, json={"autorizado": False, "version": consent["version"]}, headers=flow.headers
        ).status_code
        == 200
    )
    visible = flow.client.get(PATH + "?socioId=BANK-E2E", headers=headers)
    assert visible.status_code == 200, visible.get_json()
    row = visible.get_json()["solicitudes"][0]
    validate_history(row, "ResumenSolicitudSocio")
    assert row["estado"] == ("completada" if payment_result == "confirmado" else "vencida")
    assert row["cliente"]["nombre"] is None and row["cliente"]["numeroDocumentoEnmascarado"] is None
    assert row["estadoSeleccion"] is None and row["seleccionRestringida"] is True
    assert row["pagoEstado"] == payment_result
    assert (
        request_history(flow, request_id)["estadoSeleccion"]["aceptacion"]["ofertaId"]
        == chosen["ofertaId"]
    )


def test_history_expiry_is_authoritative_and_revocation_does_not_erase_terminal_requests(
    flow: Flow, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers, body = prepare(flow)
    pending, _ = selected_partner_request(flow, headers, body)
    rejected, _ = selected_partner_request(flow, headers, {**body, "operationId": str(uuid4())})
    decision = flow.client.put(
        f"/v1/solicitudes-cotizacion/{rejected['solicitudId']}/rechazo",
        json={},
        headers={**flow.headers, "Idempotency-Key": str(uuid4())},
    )
    assert decision.status_code == 200
    initial = request_history(flow, pending["solicitudId"])
    expiry = datetime.fromisoformat(initial["validaHasta"]) + timedelta(seconds=1)
    with crear_sesiones(flow.quote_database)() as session:
        quote = session.scalar(
            select(CotizacionModel).where(CotizacionModel.solicitud_id == pending["solicitudId"])
        )
        assert quote is not None
        assert expiry < quote.valida_hasta.replace(tzinfo=UTC)
    consent = flow.client.get(CONSENT, headers=flow.headers).get_json()
    revoked = flow.client.put(
        CONSENT, json={"autorizado": False, "version": consent["version"]}, headers=flow.headers
    )
    assert revoked.status_code == 200
    history_path = PATH + "?socioId=BANK-E2E"
    before_expiry = flow.client.get(history_path, headers=headers)
    assert before_expiry.status_code == 200
    assert {row["estado"] for row in before_expiry.get_json()["solicitudes"]} == {
        "pendiente",
        "rechazada",
    }
    monkeypatch.setattr(flow.quotes.extensions["historial_solicitudes"], "_reloj", lambda: expiry)
    assert request_history(flow, pending["solicitudId"])["estado"] == "vencida"
    assert request_history(flow, rejected["solicitudId"])["estado"] == "rechazada"
    result = flow.client.get(history_path, headers=headers)
    assert result.status_code == 200, result.get_json()
    rows = result.get_json()["solicitudes"]
    assert {row["estado"] for row in rows} == {"vencida", "rechazada"}
    assert all(
        row["estadoSeleccion"] is None
        and row["seleccionRestringida"]
        and row["cliente"]["nombre"] is None
        and row["cliente"]["tipoDocumento"] is None
        and row["cliente"]["numeroDocumentoEnmascarado"] is None
        for row in rows
    )
    assert (
        flow.client.get(
            f"{PATH}/{body['operationId']}?socioId=BANK-E2E", headers=headers
        ).status_code
        == 403
    )
    flow.authorize(version=revoked.get_json()["version"])
    restored = flow.client.get(history_path, headers=headers).get_json()["solicitudes"]
    assert {row["estado"] for row in restored} == {"vencida", "rechazada"}
    assert all(row["cliente"]["numeroDocumentoEnmascarado"].endswith("5678") for row in restored)
    assert all(row["estadoSeleccion"] is None and row["seleccionRestringida"] for row in restored)


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
            403,
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
