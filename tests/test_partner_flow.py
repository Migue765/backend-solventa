import json
import math
import os
import time
from uuid import uuid4

import pytest
from cotizacion.infrastructure.persistence.admision_socios import AdmisionSocios
from cotizacion.infrastructure.persistence.database import crear_sesiones
from cotizacion.infrastructure.persistence.models import CotizacionModel, OperacionSocioModel
from sqlalchemy import func, select

from tests import test_profiling_flow
from tests.test_profiling_flow import CONSENT, Flow

flow = test_profiling_flow.flow

PATH = "/v1/socios/solicitudes-cotizacion"


def prepare(flow: Flow) -> tuple[dict[str, str], dict]:
    created = flow.identity.test_cli_runner().invoke(
        args=[
            "create-partner-user",
            "--name",
            "Socio integrado",
            "--email",
            "socio@integracion.co",
            "--partner-id",
            "BANK-E2E",
            "--partner-name",
            "Banco sintetico",
        ],
        input="Solventa123!\nSolventa123!\n",
    )
    assert created.exit_code == 0, created.output
    login = flow.client.post(
        "/v1/sesiones",
        json={
            "correo": "socio@integracion.co",
            "contrasena": "Solventa123!",
        },
    )
    assert login.status_code == 200
    assert login.get_json()["usuario"]["rol"] == "socio"
    assert login.get_json()["usuario"]["socios"] == ["BANK-E2E"]
    partner_headers = {"Authorization": "Bearer " + login.get_json()["accessToken"]}
    flow.authorize()
    consent = flow.client.get(CONSENT, headers=flow.headers).get_json()
    current = flow.client.get("/v1/sesiones/actual", headers=flow.headers).get_json()
    body = {
        "operationId": str(uuid4()),
        "socioId": "BANK-E2E",
        "productoId": "automotive",
        "datosProducto": {"vehiclePlate": "ABC123"},
        "cliente": {
            "clienteId": current["usuario"]["id"],
            "tipoCliente": "persona_natural",
            "tipoDocumento": "cc",
            "numeroDocumento": "1023456789",
            "telefono": "+573001234567",
        },
        "consentimientoId": consent["consentimientoId"],
        "consentimientoVersion": consent["version"],
    }
    return partner_headers, body


def test_partner_login_consent_profile_quote_persistence_and_recovery(flow: Flow) -> None:
    headers, body = prepare(flow)
    context = flow.client.get("/v1/socios/actual", headers=headers)
    assert context.status_code == 200
    print("partner_context=" + json.dumps(context.get_json()))
    assert "automotive" in context.get_json()["socios"][0]["productos"]
    post_headers = {**headers, "Idempotency-Key": body["operationId"], "API-Version": "1"}
    first = flow.client.post(PATH, json=body, headers=post_headers)
    assert first.status_code == 201, first.get_json()
    retry = flow.client.post(PATH, json=body, headers=post_headers)
    assert retry.get_json() == first.get_json()
    recovered = flow.client.get(f"{PATH}/{body['operationId']}?socioId=BANK-E2E", headers=headers)
    assert recovered.status_code == 200 and recovered.get_json() == first.get_json()
    with crear_sesiones(flow.quote_database)() as session:
        assert session.scalar(select(func.count()).select_from(CotizacionModel)) == 1
        assert session.scalar(select(func.count()).select_from(OperacionSocioModel)) == 1
    request_id = first.get_json()["solicitudId"]
    customer_view = flow.client.get(
        f"/v1/solicitudes-cotizacion/{request_id}", headers=flow.headers
    )
    assert customer_view.status_code == 200
    assert customer_view.get_json()["productoId"] == "automotive"
    revoked = flow.client.put(
        CONSENT,
        json={"autorizado": False, "version": body["consentimientoVersion"]},
        headers=flow.headers,
    )
    assert revoked.status_code == 200
    new_body = {**body, "operationId": str(uuid4())}
    denied = flow.client.post(
        PATH, json=new_body, headers={**headers, "Idempotency-Key": new_body["operationId"]}
    )
    assert denied.status_code == 403
    assert denied.get_json()["code"] == "consentimiento_invalido"
    with crear_sesiones(flow.quote_database)() as session:
        assert session.scalar(select(func.count()).select_from(CotizacionModel)) == 1


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
        f"local Windows, HTTP downstream, SQLite, automotive n=100 p95={p95:.2f}ms p99={p99:.2f}ms"
    )
    assert p95 <= 250
    assert p99 <= 500
