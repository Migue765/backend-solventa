import pytest
from bff_web.domain.quote_errors import (
    PaymentNotAllowedError,
    QuoteRequestConflictError,
    QuoteRequestNotFoundError,
    QuoteServiceUnavailableError,
)
from flask import Flask
from support import FakeJwks, StubQuoteGateway, bearer

PATH = "/v1/solicitudes-cotizacion/AQT-2026-ABCDEF1234/pago-simulado"


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_payment_requires_jwt(app: Flask, method: str) -> None:
    response = app.test_client().open(PATH, method=method)
    assert response.status_code == 401
    assert response.get_json()["code"] == "sesion_invalida"


def test_missing_payment_is_distinct_and_scoped(
    app: Flask, token: str, quotes: StubQuoteGateway
) -> None:
    response = app.test_client().get(PATH, headers=bearer(token))
    assert response.status_code == 404
    assert response.get_json()["code"] == "pago_no_encontrado"
    assert len(quotes.user_ids) == 1


@pytest.mark.parametrize(
    ("body", "key"),
    [
        ({}, None),
        ({}, "invalid"),
        ({"importe": "1"}, "d6758e98-bc88-44bf-b7ce-f2f759757101"),
        (None, "d6758e98-bc88-44bf-b7ce-f2f759757101"),
    ],
)
def test_rejects_invalid_payment_input(
    app: Flask, token: str, body: object, key: str | None
) -> None:
    import json

    headers = bearer(token)
    if key is not None:
        headers["Idempotency-Key"] = key
    response = app.test_client().post(
        PATH, headers=headers, data=json.dumps(body), content_type="application/json"
    )
    assert response.status_code == 400
    assert response.get_json()["code"] == "solicitud_invalida"


def test_jwks_failure_is_not_payment_success(app: Flask, token: str, jwks: FakeJwks) -> None:
    jwks.failing = True
    assert app.test_client().get(PATH, headers=bearer(token)).status_code == 503


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (PaymentNotAllowedError, 422, "pago_no_permitido"),
        (QuoteRequestConflictError, 409, "conflicto_idempotencia"),
        (QuoteRequestNotFoundError, 404, "solicitud_no_encontrada"),
        (QuoteServiceUnavailableError, 503, "servicio_no_disponible"),
    ],
)
def test_payment_failure_is_not_success(
    app: Flask,
    token: str,
    quotes: StubQuoteGateway,
    monkeypatch: pytest.MonkeyPatch,
    error: type[Exception],
    status: int,
    code: str,
) -> None:
    from unittest.mock import Mock
    from uuid import uuid4

    monkeypatch.setattr(quotes, "confirm_simulated_payment", Mock(side_effect=error))
    response = app.test_client().post(
        PATH, headers={**bearer(token), "Idempotency-Key": str(uuid4())}, json={}
    )
    assert response.status_code == status
    assert response.content_type == "application/problem+json"
    assert response.get_json()["code"] == code
