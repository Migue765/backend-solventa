from typing import Any
from uuid import uuid4

from flask import Flask
from support import USER, FakeJwks, KeyPair, StubQuoteGateway, bearer, sign_token
from werkzeug.test import TestResponse

REQUEST = {
    "productoId": "automotive",
    "tipoCliente": "persona_natural",
    "identificacion": "12345678",
    "patenteVehiculo": "abc123",
    "correo": "cliente@solventa.co",
    "telefono": "+573001234567",
}


def body(response: TestResponse) -> dict[str, Any]:
    data = response.get_json()
    assert isinstance(data, dict)
    return data

    def list_offers(self, user_id: UUID, request_id: str) -> OffersResult:
        return OffersResult(
            request_id=request_id,
            calculated_at=datetime(2026, 10, 11, 18, 0, tzinfo=UTC),
            valid_until=datetime(2026, 10, 26, 18, 0, tzinfo=UTC),
            applied_rate="Estándar (Risk A)",
            offers=(self._offer(request_id),),
        )

    def get_selection(self, user_id: UUID, request_id: str) -> OfferSelection:
        assert self.selection is not None
        return self.selection

    def select_offer(
        self,
        user_id: UUID,
        request_id: str,
        offer_id: UUID,
        version: int,
        idempotency_key: UUID,
    ) -> OfferSelection:
        self.selection = OfferSelection(
            request_id=request_id,
            offer_id=offer_id,
            version=version,
            offer_name="Seguro Automotriz Pro",
            monthly_premium=Money(Decimal("189900"), "COP"),
            selected_at=datetime(2026, 10, 11, 18, 5, tzinfo=UTC),
        )
        return self.selection

    @staticmethod
    def _offer(request_id: str) -> Offer:
        return Offer(
            offer_id=uuid4(),
            version=1,
            name="Seguro Automotriz Pro",
            description="Cobertura extendida",
            monthly_premium=Money(Decimal("189900"), "COP"),
            main_coverage="Daños propios y terceros",
            deductible="$500.000 COP (Bajo)",
            duration="Anual",
            civil_liability="Hasta $1.000.000.000 COP",
            coverages=("Daños materiales",),
            conditions=("Sujeto a inspección",),
            additional_benefits=("Auto de reemplazo",),
            recommended=True,
            status="disponible",
            valid_until=datetime(2026, 10, 26, 18, 0, tzinfo=UTC),
        )


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
    assert body(created)["identificacion"] == "12345678"
    assert body(created)["patenteVehiculo"] == "ABC123"
    assert read.status_code == 200
    assert quotes.user_ids == [USER.user_id, USER.user_id]


def test_invalid_request_is_not_sent_to_cotizacion(
    app: Flask, token: str, quotes: StubQuoteGateway
) -> None:
    response = app.test_client().post(
        "/v1/solicitudes-cotizacion",
        json={**REQUEST, "telefono": "3001234567"},
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
