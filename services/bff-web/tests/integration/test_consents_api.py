from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from bff_web.domain.auth import User
from flask import Flask
from support import NOW, USER, FakeJwks, KeyPair, StubQuoteGateway, bearer, sign_token

PATH = "/v1/consentimientos/perfilamiento"


@pytest.mark.parametrize("authorization", [None, "Bearer invalido"])
def test_consent_still_requires_a_valid_session(app: Flask, authorization: str | None) -> None:
    headers = {"Authorization": authorization} if authorization else {}
    response = app.test_client().get(PATH, headers=headers)

    assert response.status_code == 401
    assert response.get_json()["code"] == "sesion_invalida"
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_consent_is_always_authorized_and_explicitly_simulated(app: Flask, token: str) -> None:
    response = app.test_client().get(PATH, headers=bearer(token))

    assert response.status_code == 200
    payload = response.get_json()
    assert UUID(payload["consentimientoId"])
    assert payload == {
        "consentimientoId": payload["consentimientoId"],
        "clienteId": str(USER.user_id),
        "version": 1,
        "estado": "autorizado",
        "autorizado": True,
        "finalidad": "cotizacion",
        "alcances": ["open_finance", "open_data"],
        "efectivaEn": NOW.isoformat().replace("+00:00", "Z"),
        "vigenteHasta": (NOW + timedelta(days=30)).isoformat().replace("+00:00", "Z"),
        "simulada": True,
    }
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Correlation-ID"]


def test_consent_belongs_to_the_authenticated_user(app: Flask, keypair: KeyPair) -> None:
    other = User(uuid4(), "Otro Cliente", "otro@solventa.co", "cliente")
    client = app.test_client()
    first = client.get(PATH, headers=bearer(sign_token(keypair))).get_json()
    repeated = client.get(PATH, headers=bearer(sign_token(keypair))).get_json()
    second = client.get(
        PATH + "?clienteId=" + str(USER.user_id), headers=bearer(sign_token(keypair, user=other))
    ).get_json()

    assert first["consentimientoId"] == repeated["consentimientoId"]
    assert second["clienteId"] == str(other.user_id)
    assert second["consentimientoId"] != first["consentimientoId"]


def test_consent_does_not_hide_identity_outages(app: Flask, jwks: FakeJwks, token: str) -> None:
    jwks.failing = True

    response = app.test_client().get(PATH, headers=bearer(token))

    assert response.status_code == 503
    assert response.get_json()["code"] == "servicio_no_disponible"


def test_mock_consent_allows_creating_a_quote_without_an_authorization_step(
    app: Flask, token: str, quotes: StubQuoteGateway
) -> None:
    client = app.test_client()
    consent = client.get(PATH, headers=bearer(token))
    created = client.post(
        "/v1/solicitudes-cotizacion",
        json={"productoId": "automotive", "datosProducto": {"vehiclePlate": "ABC123"}},
        headers={**bearer(token), "Idempotency-Key": str(uuid4())},
    )

    assert consent.get_json()["autorizado"] is True
    assert created.status_code == 201
    assert quotes.user_ids == [USER.user_id]
