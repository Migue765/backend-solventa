import json
from email.message import Message
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest
from bff_web.infrastructure.http_quote_gateway import HttpQuoteGateway
from flask import Flask
from support import USER, bearer


def snapshot() -> dict[str, object]:
    return {
        "solicitudId": "AQT-test",
        "ofertaId": str(uuid4()),
        "version": 1,
        "nombreOferta": "Plan sintetico",
        "prima": {"monto": "100.00", "moneda": "COP"},
        "primaMensual": {"monto": "100.00", "moneda": "COP"},
        "periodicidad": "mensual",
        "seleccionadaEn": "2026-11-01T12:00:00Z",
        "revision": 1,
        "vigenteHasta": "2026-11-15T12:00:00Z",
    }


@pytest.mark.parametrize("method", ["GET", "PUT"])
def test_provisional_requires_bearer(app: Flask, method: str) -> None:
    response = app.test_client().open(
        "/v1/solicitudes-cotizacion/AQT-test/seleccion-provisional", method=method
    )
    assert response.status_code == 401


@pytest.mark.parametrize("revision", [None, True, 0, "1"])
def test_invalid_acceptance_revision_never_reaches_downstream(
    app: Flask, token: str, revision: object
) -> None:
    app.extensions["quote_gateway"] = HttpQuoteGateway("http://cotizacion")
    with patch("bff_web.infrastructure.http_quote_gateway.urlopen") as downstream:
        response = app.test_client().put(
            "/v1/solicitudes-cotizacion/AQT-test/seleccion",
            json={"ofertaId": str(uuid4()), "version": 1, "revisionSeleccion": revision},
            headers={**bearer(token), "Idempotency-Key": str(uuid4())},
        )
    assert response.status_code == 400
    downstream.assert_not_called()


def test_provisional_http_gateway_preserves_revision_snapshot_and_owner(
    app: Flask, token: str
) -> None:
    app.extensions["quote_gateway"] = HttpQuoteGateway("http://cotizacion")
    saved = snapshot()
    key = str(uuid4())
    with patch(
        "bff_web.infrastructure.http_quote_gateway.urlopen",
        return_value=BytesIO(json.dumps(saved).encode()),
    ) as downstream:
        response = app.test_client().put(
            "/v1/solicitudes-cotizacion/AQT-test/seleccion-provisional?clienteId=otro",
            json={"ofertaId": saved["ofertaId"], "version": 1, "revisionEsperada": 0},
            headers={**bearer(token), "Idempotency-Key": key},
        )
    assert response.status_code == 200
    assert response.get_json() == saved
    assert response.headers["Cache-Control"] == "no-store"
    request = downstream.call_args.args[0]
    assert parse_qs(urlparse(request.full_url).query) == {"clienteId": [str(USER.user_id)]}
    assert request.get_method() == "PUT"
    assert request.headers["Idempotency-key"] == key
    assert json.loads(request.data)["revisionEsperada"] == 0


@pytest.mark.parametrize("selected", [False, True])
@pytest.mark.parametrize("accepted", [False, True])
def test_provisional_resume_preserves_selection_and_existing_acceptance(
    app: Flask, token: str, selected: bool, accepted: bool
) -> None:
    app.extensions["quote_gateway"] = HttpQuoteGateway("http://cotizacion")
    acceptance = snapshot()
    del acceptance["revision"]
    del acceptance["vigenteHasta"]
    expected = {
        "solicitudId": "AQT-test",
        "seleccion": snapshot() if selected else None,
        "aceptacion": acceptance if accepted else None,
        "estadoOferta": ("aceptada" if accepted else "disponible") if selected else None,
    }
    with patch(
        "bff_web.infrastructure.http_quote_gateway.urlopen",
        return_value=BytesIO(json.dumps(expected).encode()),
    ) as downstream:
        response = app.test_client().get(
            "/v1/solicitudes-cotizacion/AQT-test/seleccion-provisional", headers=bearer(token)
        )
    assert response.status_code == 200
    assert response.get_json() == expected
    assert downstream.call_count == 1
    assert downstream.call_args.args[0].get_method() == "GET"


def test_acceptance_forwards_revision_without_changing_legacy_response(
    app: Flask, token: str
) -> None:
    app.extensions["quote_gateway"] = HttpQuoteGateway("http://cotizacion")
    saved = snapshot()
    del saved["revision"]
    del saved["vigenteHasta"]
    with patch(
        "bff_web.infrastructure.http_quote_gateway.urlopen",
        return_value=BytesIO(json.dumps(saved).encode()),
    ) as downstream:
        response = app.test_client().put(
            "/v1/solicitudes-cotizacion/AQT-test/seleccion",
            json={"ofertaId": saved["ofertaId"], "version": 1, "revisionSeleccion": 2},
            headers={**bearer(token), "Idempotency-Key": str(uuid4())},
        )
    assert response.status_code == 200
    assert response.get_json() == saved
    assert json.loads(downstream.call_args.args[0].data)["revisionSeleccion"] == 2
    assert downstream.call_count == 1


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (409, "seleccion_desactualizada"),
        (409, "seleccion_no_modificable"),
        (409, "conflicto_idempotencia"),
        (422, "oferta_no_disponible"),
        (403, "consentimiento_no_vigente"),
    ],
)
@pytest.mark.parametrize("suffix", ["seleccion", "seleccion-provisional"])
def test_selection_rejections_preserve_code_and_do_not_start_subscription(
    app: Flask, token: str, status: int, code: str, suffix: str
) -> None:
    app.extensions["quote_gateway"] = HttpQuoteGateway("http://cotizacion")
    error = HTTPError(
        "http://cotizacion", status, code, Message(), BytesIO(json.dumps({"error": code}).encode())
    )
    payload: dict[str, object] = {"ofertaId": str(uuid4()), "version": 1}
    payload["revisionEsperada" if suffix.endswith("provisional") else "revisionSeleccion"] = 1
    with patch(
        "bff_web.infrastructure.http_quote_gateway.urlopen", side_effect=error
    ) as downstream:
        response = app.test_client().put(
            "/v1/solicitudes-cotizacion/AQT-test/" + suffix,
            json=payload,
            headers={**bearer(token), "Idempotency-Key": str(uuid4())},
        )
    assert response.status_code == status
    assert response.get_json()["code"] == code
    assert response.mimetype == "application/problem+json"
    assert set(response.get_json()) == {
        "type",
        "title",
        "status",
        "detail",
        "code",
        "correlationId",
    }
    assert response.get_json()["type"] == f"https://api.solventa.co/problems/{code}"
    assert response.get_json()["status"] == status
    assert response.get_json()["correlationId"]
    assert response.headers["Cache-Control"] == "no-store"
    assert downstream.call_count == 1
