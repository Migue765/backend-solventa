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
from bff_web.domain.quotes import (
    PendingSimulatedPayment,
    QuoteRequestDraft,
    RejectedSimulatedPayment,
)
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


def rejected_payment_payload() -> dict[str, object]:
    identifier = str(uuid4())
    return {
        "id": identifier,
        "solicitudId": "AQT-test",
        "suscripcionId": str(uuid4()),
        "ofertaId": str(uuid4()),
        "ofertaVersion": 1,
        "estado": "rechazado",
        "simulada": True,
        "referenciaInterna": f"SIM-{identifier}",
        "importe": {"monto": "100.00", "moneda": "COP"},
        "periodicidad": "mensual",
        "rechazadaEn": "2026-11-01T12:00:01Z",
        "motivo": "rechazo_simulado",
        "saga": {"estado": "pago_pendiente", "version": 1, "actualizadaEn": "2026-11-01T12:00:00Z"},
    }


def pending_payment_payload() -> dict[str, object]:
    result = rejected_payment_payload()
    result.pop("rechazadaEn")
    result.pop("motivo")
    return {
        **result,
        "estado": "pendiente",
        "creadaEn": "2026-11-01T12:00:01Z",
        "codigo": "resultado_no_confirmado",
    }


@pytest.mark.parametrize("method", ["POST", "GET"])
def test_P14_pending_is_typed_and_get_forwards_original_key(method: str) -> None:
    key = uuid4()
    payload = pending_payment_payload()
    with patch(
        "bff_web.infrastructure.http_quote_gateway.urlopen",
        return_value=BytesIO(json.dumps(payload).encode()),
    ) as call:
        gateway = HttpQuoteGateway("http://cotizacion")
        result = (
            gateway.confirm_simulated_payment(uuid4(), "AQT-test", key)
            if method == "POST"
            else gateway.get_simulated_payment(uuid4(), "AQT-test", key)
        )
    assert isinstance(result, PendingSimulatedPayment)
    assert str(result.payment_id) == payload["id"] and result.saga.status == "pago_pendiente"
    assert call.call_args.args[0].get_header("Idempotency-key") == str(key)


@pytest.mark.parametrize(
    "changes",
    [
        {"confirmadaEn": "2026-11-01T12:00:01Z"},
        {"rechazadaEn": "2026-11-01T12:00:01Z"},
        {"motivo": "rechazo_simulado"},
        {"estado": "desconocido"},
        {"simulada": 1},
        {"codigo": "confirmado"},
        {"id": {}},
        {"suscripcionId": "invalido"},
        {"ofertaId": []},
        {"ofertaVersion": True},
        {"ofertaVersion": 0},
        {"solicitudId": ""},
        {"creadaEn": None},
        {"creadaEn": "2026-11-01T12:00:00"},
        {"creadaEn": "2026-10-01T12:00:00Z"},
        {"importe": {"monto": "NaN", "moneda": "COP"}},
        {"importe": {"monto": "0.00", "moneda": "COP"}},
        {"importe": {"monto": 100, "moneda": "COP"}},
        {"importe": {"monto": "100.001", "moneda": "COP"}},
        {"importe": {"monto": "100.00", "moneda": "cop"}},
        {"importe": {"monto": "100.00", "moneda": "123"}},
        {
            "saga": {
                "estado": "pago_confirmado",
                "version": 2,
                "actualizadaEn": "2026-11-01T12:00:00Z",
            }
        },
        {
            "saga": {
                "estado": "pago_pendiente",
                "version": True,
                "actualizadaEn": "2026-11-01T12:00:00Z",
            }
        },
        {"saga": {"estado": "pago_pendiente", "version": 1, "actualizadaEn": None}},
    ],
)
def test_P14_pending_rejects_invalid_shapes(changes: dict[str, object]) -> None:
    payload = {**pending_payment_payload(), **changes}
    with (
        patch(
            "bff_web.infrastructure.http_quote_gateway.urlopen",
            return_value=BytesIO(json.dumps(payload).encode()),
        ),
        pytest.raises(QuoteServiceUnavailableError),
    ):
        HttpQuoteGateway("http://cotizacion").get_simulated_payment(uuid4(), "AQT-test", uuid4())


@pytest.mark.parametrize("method", ["POST", "GET"])
def test_http_gateway_preserves_rejected_result(method: str) -> None:
    payload = rejected_payment_payload()
    with patch(
        "bff_web.infrastructure.http_quote_gateway.urlopen",
        return_value=BytesIO(json.dumps(payload).encode()),
    ):
        gateway = HttpQuoteGateway("http://cotizacion")
        result = (
            gateway.confirm_simulated_payment(uuid4(), "AQT-test", uuid4())
            if method == "POST"
            else gateway.get_simulated_payment(uuid4(), "AQT-test")
        )
    assert isinstance(result, RejectedSimulatedPayment)
    assert str(result.payment_id) == payload["id"]
    assert result.saga.status == "pago_pendiente"


@pytest.mark.parametrize(
    "changes",
    [
        {"confirmadaEn": "2026-11-01T12:00:01Z"},
        {"estado": "pendiente"},
        {"simulada": False},
        {"motivo": "aprobado"},
        {"rechazadaEn": None},
        {"rechazadaEn": "2026-11-01T12:00:00"},
        {"importe": {"monto": "0", "moneda": "COP"}},
        {"saga": {"estado": "emitida", "version": 3}},
        {
            "saga": {
                "estado": "pago_pendiente",
                "version": True,
                "actualizadaEn": "2026-11-01T12:00:00Z",
            }
        },
        {"saga": {"estado": "pago_pendiente", "version": 1, "actualizadaEn": None}},
        {
            "saga": {
                "estado": "pago_pendiente",
                "version": 1,
                "actualizadaEn": "2026-11-01T12:00:00",
            }
        },
    ],
)
def test_http_gateway_does_not_accept_inconsistent_rejection(changes: dict[str, object]) -> None:
    payload = {**rejected_payment_payload(), **changes}
    with patch(
        "bff_web.infrastructure.http_quote_gateway.urlopen",
        return_value=BytesIO(json.dumps(payload).encode()),
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
