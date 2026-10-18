import json
from datetime import UTC, datetime
from email.message import Message
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError
from uuid import uuid4

import pytest
from bff_web.domain.quote_errors import (
    PaymentNotAllowedError,
    QuoteRequestConflictError,
    QuoteRequestNotFoundError,
    QuoteServiceUnavailableError,
    SimulatedPaymentNotFoundError,
)
from bff_web.domain.quotes import QuoteRequestDraft
from bff_web.infrastructure.http_quote_gateway import HttpQuoteGateway


@pytest.mark.parametrize(
    ("status", "code", "expected"),
    [
        (404, "pago_no_encontrado", SimulatedPaymentNotFoundError),
        (404, "no_encontrado", QuoteRequestNotFoundError),
        (422, "pago_no_permitido", PaymentNotAllowedError),
        (409, "conflicto_idempotencia", QuoteRequestConflictError),
        (503, "no_disponible", QuoteServiceUnavailableError),
    ],
)
def test_maps_payment_errors(status: int, code: str, expected: type[Exception]) -> None:
    error = HTTPError(
        "http://cotizacion", status, code, Message(), BytesIO(json.dumps({"error": code}).encode())
    )
    with patch("bff_web.infrastructure.http_quote_gateway.urlopen", side_effect=error):
        with pytest.raises(expected):
            HttpQuoteGateway("http://cotizacion").get_simulated_payment(uuid4(), "AQT-test")


@pytest.mark.parametrize("simulada", [False, None, "true"])
def test_never_turns_unmarked_gateway_response_into_simulated_success(simulada: object) -> None:
    response = {"simulada": simulada, "estado": "confirmado"}
    with patch(
        "bff_web.infrastructure.http_quote_gateway.urlopen",
        return_value=BytesIO(json.dumps(response).encode()),
    ):
        with pytest.raises(QuoteServiceUnavailableError):
            HttpQuoteGateway("http://cotizacion").get_simulated_payment(uuid4(), "AQT-test")


def test_reads_the_supported_travel_destinations() -> None:
    response = {
        "destinos": [
            {"codigo": "CO", "nombre": "Colombia"},
            {"codigo": "ES", "nombre": "España"},
        ]
    }

    with patch(
        "bff_web.infrastructure.http_quote_gateway.urlopen",
        return_value=BytesIO(json.dumps(response).encode()),
    ):
        destinations = HttpQuoteGateway("http://cotizacion").list_travel_destinations()

    assert [(destination.code, destination.name) for destination in destinations] == [
        ("CO", "Colombia"),
        ("ES", "España"),
    ]


def test_propagates_product_data_when_creating_and_reading_a_request() -> None:
    user_id = uuid4()
    product_data = {
        "destinationCountryCode": "ES",
        "departureDate": "2026-11-01",
        "returnDate": "2026-11-08",
    }
    draft = QuoteRequestDraft(
        product_id="travel",
        customer_type="persona_natural",
        document_type="cc",
        identification="1234567890",
        product_data=product_data,
        email="cliente@solventa.co",
        phone="+573001234567",
    )
    response = {
        "id": "AQT-2026-ABCDEF1234",
        "productoId": "travel",
        "tipoCliente": "persona_natural",
        "tipoDocumento": "cc",
        "identificacion": "1234567890",
        "datosProducto": product_data,
        "correo": "cliente@solventa.co",
        "telefono": "+573001234567",
        "estado": "recibida",
        "creadaEn": datetime(2026, 10, 14, 18, 0, tzinfo=UTC).isoformat(),
    }

    with patch(
        "bff_web.infrastructure.http_quote_gateway.urlopen",
        return_value=BytesIO(json.dumps(response).encode()),
    ) as urlopen:
        record = HttpQuoteGateway("http://cotizacion").create_request(user_id, draft, uuid4())

    sent_request = urlopen.call_args.args[0]
    assert json.loads(sent_request.data) == {
        "clienteId": str(user_id),
        "productoId": "travel",
        "tipoCliente": "persona_natural",
        "tipoDocumento": "cc",
        "identificacion": "1234567890",
        "datosProducto": product_data,
        "correo": "cliente@solventa.co",
        "telefono": "+573001234567",
    }
    assert record.draft.product_data == product_data


@pytest.mark.parametrize(
    ("product_id", "data"),
    [
        (
            "parametric",
            {
                "airline": "Aerolínea de prueba",
                "flightNumber": "AV123",
                "flightDate": "2020-01-01",
            },
        ),
        (
            "parametric-weather",
            {
                "location": "Medellín, Antioquia",
                "weatherEvent": "drought",
                "startDate": "2020-01-01",
                "endDate": "2020-02-01",
                "insuredAmount": "10000000",
            },
        ),
    ],
)
def test_reads_historical_requests_without_requiring_future_dates(
    product_id: str, data: dict[str, str]
) -> None:
    request_id = "AQT-2020-ABCDEF1234"
    response = {
        "id": request_id,
        "productoId": product_id,
        "tipoCliente": "persona_natural",
        "tipoDocumento": "cc",
        "identificacion": "1234567890",
        "datosProducto": data,
        "correo": "cliente@solventa.co",
        "telefono": "+573001234567",
        "estado": "recibida",
        "creadaEn": "2019-12-01T12:00:00Z",
    }
    with patch(
        "bff_web.infrastructure.http_quote_gateway.urlopen",
        return_value=BytesIO(json.dumps(response).encode()),
    ):
        record = HttpQuoteGateway("http://cotizacion").get_request(uuid4(), request_id)
    assert record.draft.product_data == data
