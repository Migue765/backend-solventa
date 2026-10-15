import json
from datetime import UTC, datetime
from io import BytesIO
from unittest.mock import patch
from uuid import uuid4

from bff_web.domain.quotes import QuoteRequestDraft
from bff_web.infrastructure.http_quote_gateway import HttpQuoteGateway


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
