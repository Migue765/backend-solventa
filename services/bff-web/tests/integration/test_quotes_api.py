from datetime import date, timedelta
from typing import Any
from uuid import uuid4

import pytest
from flask import Flask
from support import USER, FakeJwks, KeyPair, StubQuoteGateway, bearer, sign_token
from werkzeug.test import TestResponse

REQUEST = {
    "productoId": "automotive",
    "datosProducto": {"vehiclePlate": "ABC123"},
}


def body(response: TestResponse) -> dict[str, Any]:
    data = response.get_json()
    assert isinstance(data, dict)
    return data


def test_every_quote_endpoint_requires_a_bearer_token(app: Flask) -> None:
    client = app.test_client()
    headers = {"Idempotency-Key": str(uuid4())}

    responses = [
        client.get("/v1/productos-seguro"),
        client.post("/v1/solicitudes-cotizacion", json=REQUEST, headers=headers),
        client.get("/v1/solicitudes-cotizacion/AQT-2026-ABCDEF1234"),
    ]

    for response in responses:
        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == "Bearer"
        assert body(response)["code"] == "sesion_invalida"


def test_cookies_no_longer_authenticate(app: Flask, token: str) -> None:
    client = app.test_client()
    client.set_cookie("solventa_session", token)

    assert client.get("/v1/productos-seguro").status_code == 401


def test_authenticated_client_lists_products(app: Flask, token: str) -> None:
    response = app.test_client().get("/v1/productos-seguro", headers=bearer(token))

    assert response.status_code == 200
    assert body(response)["productos"][0]["id"] == "automotive"


def test_request_is_created_and_read_with_the_subject_of_the_token(
    app: Flask, token: str, quotes: StubQuoteGateway
) -> None:
    client = app.test_client()
    created = client.post(
        "/v1/solicitudes-cotizacion",
        json=REQUEST,
        headers={**bearer(token), "Idempotency-Key": str(uuid4())},
    )
    read = client.get("/v1/solicitudes-cotizacion/AQT-2026-ABCDEF1234", headers=bearer(token))

    assert created.status_code == 201
    assert body(created)["identificacion"] == USER.identification
    assert body(created)["datosProducto"] == {"vehiclePlate": "ABC123"}
    assert read.status_code == 200
    assert quotes.user_ids == [USER.user_id, USER.user_id]


def test_invalid_request_is_not_sent_to_cotizacion(
    app: Flask, token: str, quotes: StubQuoteGateway
) -> None:
    response = app.test_client().post(
        "/v1/solicitudes-cotizacion",
        json={**REQUEST, "datosProducto": {"vehiclePlate": "abc"}},
        headers={**bearer(token), "Idempotency-Key": str(uuid4())},
    )

    assert response.status_code == 400
    assert quotes.record is None


def test_missing_idempotency_key_is_rejected(app: Flask, token: str) -> None:
    response = app.test_client().post(
        "/v1/solicitudes-cotizacion", json=REQUEST, headers=bearer(token)
    )

    assert response.status_code == 400
    assert body(response)["code"] == "solicitud_invalida"


def test_each_token_is_scoped_by_its_own_subject(
    app: Flask, token: str, keypair: KeyPair, quotes: StubQuoteGateway
) -> None:
    from bff_web.domain.auth import User

    other = User(uuid4(), "Otra Persona", "otra@solventa.co", "cliente")
    client = app.test_client()
    client.post(
        "/v1/solicitudes-cotizacion",
        json=REQUEST,
        headers={**bearer(token), "Idempotency-Key": str(uuid4())},
    )

    client.get(
        "/v1/solicitudes-cotizacion/AQT-2026-ABCDEF1234",
        headers=bearer(sign_token(keypair, user=other)),
    )

    assert quotes.user_ids == [USER.user_id, other.user_id]


def test_jwks_outage_on_quotes_is_a_503(app: Flask, jwks: FakeJwks, token: str) -> None:
    jwks.failing = True

    response = app.test_client().get("/v1/productos-seguro", headers=bearer(token))

    assert response.status_code == 503


PRODUCT_DATA = {
    "automotive": {"vehiclePlate": "ABC123"},
    "travel": {
        "destinationCountryCode": "CO",
        "departureDate": (date.today() + timedelta(days=1)).isoformat(),
        "returnDate": (date.today() + timedelta(days=30)).isoformat(),
    },
    "life": {"birthDate": "1990-06-15"},
    "device-protection": {
        "deviceType": "Teléfono",
        "brandModel": "Acme",
        "purchaseValue": "4500000",
    },
    "parametric": {
        "airline": "Aerolínea de prueba",
        "flightNumber": "AV123",
        "flightDate": (date.today() + timedelta(days=1)).isoformat(),
    },
    "parametric-weather": {
        "location": "Medellín, Antioquia",
        "weatherEvent": "heavy-rain",
        "startDate": (date.today() + timedelta(days=1)).isoformat(),
        "endDate": (date.today() + timedelta(days=30)).isoformat(),
        "insuredAmount": "10000000",
    },
    "payment-protection": {"creditType": "Consumo", "monthlyPayment": "850000"},
    "mortgage-life": {"birthDate": "1985-02-10", "mortgageBalance": "180000000"},
}
VARIANTS = [
    *((product, data) for product, data in PRODUCT_DATA.items()),
    *(
        ("parametric", {**PRODUCT_DATA["parametric"], "coverageType": coverage})
        for coverage in ("delay-cancellation", "missed-connection", "comprehensive")
    ),
    ("parametric-weather", {**PRODUCT_DATA["parametric-weather"], "weatherEvent": "drought"}),
]


@pytest.mark.parametrize(("product_id", "data"), VARIANTS)
def test_creates_all_variants_with_authenticated_identity(
    app: Flask, token: str, quotes: StubQuoteGateway, product_id: str, data: dict[str, str]
) -> None:
    client = app.test_client()
    response = client.post(
        "/v1/solicitudes-cotizacion",
        json={"productoId": product_id, "datosProducto": data},
        headers={**bearer(token), "Idempotency-Key": str(uuid4())},
    )

    assert response.status_code == 201
    assert body(response)["productoId"] == product_id
    assert body(response)["datosProducto"] == data
    assert body(response)["identificacion"] == USER.identification
    assert body(response)["correo"] == USER.email
    assert body(response)["telefono"] == USER.phone
    assert quotes.user_ids == [USER.user_id]
    read = client.get(f"/v1/solicitudes-cotizacion/{body(response)['id']}", headers=bearer(token))
    assert body(read) == body(response)


@pytest.mark.parametrize("product_id", PRODUCT_DATA)
@pytest.mark.parametrize("invalid", ["extra", "missing", "cross-product", "identity"])
def test_rejects_cross_product_data_and_client_identity(
    app: Flask, token: str, quotes: StubQuoteGateway, product_id: str, invalid: str
) -> None:
    data = dict(PRODUCT_DATA[product_id])
    payload: dict[str, Any] = {"productoId": product_id, "datosProducto": data}
    if invalid == "extra":
        data["unexpected"] = "no"
    elif invalid == "missing":
        data.pop(next(iter(data)))
    elif invalid == "cross-product":
        payload["datosProducto"] = PRODUCT_DATA["life" if product_id != "life" else "automotive"]
    else:
        payload["clienteId"] = str(uuid4())
    response = app.test_client().post(
        "/v1/solicitudes-cotizacion",
        json=payload,
        headers={**bearer(token), "Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 400
    assert quotes.record is None
