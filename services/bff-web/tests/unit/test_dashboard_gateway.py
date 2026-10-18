import json
from copy import deepcopy
from email.message import Message
from io import BytesIO
from typing import Any
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from uuid import uuid4

import pytest
from bff_web.domain.quote_errors import QuoteServiceUnavailableError
from bff_web.infrastructure.http_quote_gateway import HttpQuoteGateway


def payload() -> dict[str, Any]:
    return {
        "cotizaciones": [
            {
                "id": str(uuid4()),
                "solicitudId": None,
                "productoId": "travel",
                "estado": "CALCULADA",
                "creadaEn": "2026-10-18T10:00:00Z",
                "validaHasta": "2026-10-30T10:00:00Z",
                "primaEstimada": {"monto": "100.00", "moneda": "COP"},
                "seleccion": None,
            }
        ],
        "suscripciones": [],
        "pagos": [
            {
                "id": str(uuid4()),
                "solicitudId": "AQT-2026-TEST123456",
                "suscripcionId": str(uuid4()),
                "estado": "confirmado",
                "simulada": True,
                "importe": {"monto": "100.00", "moneda": "COP"},
                "periodicidad": "por_viaje",
                "confirmadaEn": "2026-10-18T10:00:00Z",
            }
        ],
    }


@pytest.mark.parametrize(
    ("collection", "key", "value"),
    [
        ("cotizaciones", "id", "invalid"),
        ("cotizaciones", "creadaEn", "2026-10-18T10:00:00"),
        ("cotizaciones", "primaEstimada", {"monto": 123.45, "moneda": "COP"}),
        ("cotizaciones", "primaEstimada", {"monto": "-1.00", "moneda": "COP"}),
        ("cotizaciones", "primaEstimada", {"monto": "NaN", "moneda": "COP"}),
        ("pagos", "simulada", False),
        ("pagos", "simulada", 1),
        ("pagos", "estado", "pendiente"),
        ("pagos", "periodicidad", "mensual_inventada"),
        ("pagos", "importe", {"monto": "0.00", "moneda": "COP"}),
        ("pagos", "importe", {"monto": "100.00", "moneda": "inventada"}),
    ],
)
def test_dashboard_rejects_invalid_dependency_fields(
    collection: str, key: str, value: object
) -> None:
    invalid = deepcopy(payload())
    invalid[collection][0][key] = value
    with patch(
        "bff_web.infrastructure.http_quote_gateway.urlopen",
        return_value=BytesIO(json.dumps(invalid).encode()),
    ):
        with pytest.raises(QuoteServiceUnavailableError):
            HttpQuoteGateway("http://cotizacion", internal_api_token="test").get_dashboard(uuid4())


@pytest.mark.parametrize(
    "body",
    [
        b"{}",
        b"null",
        b"[]",
        b"invalid",
        b'{"cotizaciones":null,"suscripciones":[],"pagos":[]}',
        b'{"cotizaciones":[],"suscripciones":[]}',
        b'{"cotizaciones":[],"suscripciones":[],"pagos":[],"extra":true}',
    ],
)
def test_dashboard_never_defaults_missing_or_malformed_collections(body: bytes) -> None:
    with patch("bff_web.infrastructure.http_quote_gateway.urlopen", return_value=BytesIO(body)):
        with pytest.raises(QuoteServiceUnavailableError):
            HttpQuoteGateway("http://cotizacion", internal_api_token="test").get_dashboard(uuid4())


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 422, 500, 503])
def test_dashboard_dependency_http_errors_are_unavailable(status: int) -> None:
    error = HTTPError(
        "http://cotizacion", status, "failure", Message(), BytesIO(b'{"error":"regla_de_negocio"}')
    )
    with patch("bff_web.infrastructure.http_quote_gateway.urlopen", side_effect=error):
        with pytest.raises(QuoteServiceUnavailableError):
            HttpQuoteGateway("http://cotizacion", internal_api_token="test").get_dashboard(uuid4())


@pytest.mark.parametrize("error", [URLError("offline"), TimeoutError(), ConnectionResetError()])
def test_dashboard_transport_errors_are_unavailable(error: Exception) -> None:
    with patch("bff_web.infrastructure.http_quote_gateway.urlopen", side_effect=error):
        with pytest.raises(QuoteServiceUnavailableError):
            HttpQuoteGateway("http://cotizacion", internal_api_token="test").get_dashboard(uuid4())


def test_dashboard_does_not_send_request_without_internal_credential() -> None:
    with patch("bff_web.infrastructure.http_quote_gateway.urlopen") as request:
        with pytest.raises(QuoteServiceUnavailableError):
            HttpQuoteGateway("http://cotizacion").get_dashboard(uuid4())
        request.assert_not_called()


def test_dashboard_preserves_decimal_precision_and_uses_only_internal_identity() -> None:
    response = payload()
    response["pagos"][0]["importe"]["monto"] = "9999999999999999.99"
    user_id = uuid4()
    with patch(
        "bff_web.infrastructure.http_quote_gateway.urlopen",
        return_value=BytesIO(json.dumps(response).encode()),
    ) as request:
        dashboard = HttpQuoteGateway(
            "http://cotizacion", internal_api_token="test-internal"
        ).get_dashboard(user_id)
    assert dashboard.model_dump(mode="json", by_alias=True)["pagos"][0]["importe"] == {
        "monto": "9999999999999999.99",
        "moneda": "COP",
    }
    sent = request.call_args.args[0]
    assert sent.full_url == f"http://cotizacion/v1/dashboard?clienteId={user_id}"
    assert sent.headers["Authorization"] == "Bearer test-internal"
