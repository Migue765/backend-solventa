import json
from email.message import Message
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError
from uuid import uuid4

import pytest
from bff_web.domain.mortgage_profiling import MortgageProfilingError
from bff_web.infrastructure.http_partner_quote_gateway import HttpPartnerQuoteGateway
from support import partner_offer_payload

TARGET = "bff_web.infrastructure.http_partner_quote_gateway.urlopen"


def test_calculates_offer_against_the_partner_operation() -> None:
    operation_id = uuid4()
    with patch(
        TARGET, return_value=BytesIO(json.dumps(partner_offer_payload(operation_id)).encode())
    ) as urlopen:
        offer = HttpPartnerQuoteGateway("http://cotizacion/", "token").calculate_offer(
            "BANCO-001", operation_id, "corr-1"
        )
    request = urlopen.call_args.args[0]
    assert request.get_method() == "POST"
    assert request.full_url == (
        f"http://cotizacion/v1/socios/solicitudes-cotizacion/{operation_id}/oferta"
        "?socioId=BANCO-001"
    )
    assert request.get_header("X-correlation-id") == "corr-1"
    assert offer.operationId == operation_id
    assert offer.ofertas[0].prima.monto == "40125.00"


def test_rejects_an_offer_that_breaks_the_contract() -> None:
    body = partner_offer_payload(uuid4())
    body["ofertas"][0]["factores"] = []
    with patch(TARGET, return_value=BytesIO(json.dumps(body).encode())):
        with pytest.raises(MortgageProfilingError) as error:
            HttpPartnerQuoteGateway("http://cotizacion", "token").get_offer(
                "BANCO-001", uuid4(), "corr-1"
            )
    assert (error.value.code, error.value.status) == ("servicio_no_disponible", 503)


@pytest.mark.parametrize(
    ("status", "code", "expected"),
    [
        (404, "oferta_no_encontrada", ("oferta_no_encontrada", 404)),
        (404, "operacion_no_encontrada", ("operacion_no_encontrada", 404)),
        (422, "cotizacion_vencida", ("cotizacion_vencida", 422)),
        (422, "decision_inconsistente", ("decision_inconsistente", 422)),
        (403, "consentimiento_no_vigente", ("consentimiento_no_vigente", 403)),
        (503, "perfilamiento_no_disponible", ("perfilamiento_no_disponible", 503)),
        (422, "inesperado", ("servicio_no_disponible", 503)),
        (500, "error_interno", ("servicio_no_disponible", 503)),
    ],
)
def test_maps_only_controlled_offer_errors(
    status: int, code: str, expected: tuple[str, int]
) -> None:
    error = HTTPError(
        "http://cotizacion", status, code, Message(), BytesIO(json.dumps({"error": code}).encode())
    )
    with patch(TARGET, side_effect=error):
        with pytest.raises(MortgageProfilingError) as raised:
            HttpPartnerQuoteGateway("http://cotizacion", "token").calculate_offer(
                "BANCO-001", uuid4(), "corr-1"
            )
    assert (raised.value.code, raised.value.status) == expected
