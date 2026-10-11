from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from bff_web.app import create_app
from bff_web.application.quote_ports import QuoteGateway
from bff_web.config import Config
from bff_web.domain.auth import User
from bff_web.domain.quotes import (
    InsuranceProduct,
    Money,
    Offer,
    OfferSelection,
    OffersResult,
    QuoteRequestDraft,
    QuoteRequestRecord,
)
from bff_web.infrastructure.development_credentials import DevelopmentCredential
from bff_web.infrastructure.passwords import hash_password
from flask import Flask
from flask.testing import FlaskClient

PASSWORD = "Solventa123!"


class StubQuoteGateway(QuoteGateway):
    def __init__(self) -> None:
        self.record: QuoteRequestRecord | None = None
        self.selection: OfferSelection | None = None

    def list_products(self) -> tuple[InsuranceProduct, ...]:
        return (
            InsuranceProduct(
                "automotive",
                "Seguro Automotriz Total",
                "Protección para el vehículo",
                True,
            ),
        )

    def create_request(
        self,
        user_id: UUID,
        draft: QuoteRequestDraft,
        idempotency_key: UUID,
    ) -> QuoteRequestRecord:
        self.record = QuoteRequestRecord(
            "AQT-2026-ABCDEF1234",
            draft,
            "recibida",
            datetime(2026, 10, 11, 18, 0, tzinfo=UTC),
        )
        return self.record

    def get_request(self, user_id: UUID, request_id: str) -> QuoteRequestRecord:
        assert self.record is not None
        return self.record

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
            monthly_premium=Money(Decimal("1.8000"), "UF"),
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
            monthly_premium=Money(Decimal("1.8000"), "UF"),
            main_coverage="Daños propios y terceros",
            deductible="UF 3 (Bajo)",
            duration="Anual",
            civil_liability="Hasta UF 1.000",
            coverages=("Daños materiales",),
            conditions=("Sujeto a inspección",),
            additional_benefits=("Auto de reemplazo",),
            recommended=True,
            status="disponible",
            valid_until=datetime(2026, 10, 26, 18, 0, tzinfo=UTC),
        )


@pytest.fixture
def app() -> Flask:
    user = User(uuid4(), "Cliente Solventa", "cliente@solventa.co", "cliente")
    return create_app(
        Config(
            credentials=(
                DevelopmentCredential(
                    user,
                    hash_password(PASSWORD, salt=b"0123456789abcdef"),
                ),
            ),
            session_cookie_secure=False,
            session_duration=timedelta(minutes=30),
        ),
        quote_gateway=StubQuoteGateway(),
    )


def login(client: FlaskClient) -> None:
    response = client.post(
        "/v1/sesiones",
        json={"correo": "cliente@solventa.co", "contrasena": PASSWORD},
    )
    assert response.status_code == 200


def request_payload() -> dict[str, str]:
    return {
        "productoId": "automotive",
        "tipoCliente": "persona_natural",
        "identificacion": "12.345.678-9",
        "patenteVehiculo": "abc123",
        "correo": "CLIENTE@SOLVENTA.CO",
        "telefono": "+56912345678",
    }


def test_catalog_requires_authentication(app: Flask) -> None:
    assert app.test_client().get("/v1/productos-seguro").status_code == 401


def test_creates_and_reads_an_authenticated_quote_request(app: Flask) -> None:
    client = app.test_client()
    login(client)

    created = client.post(
        "/v1/solicitudes-cotizacion",
        json=request_payload(),
        headers={"Idempotency-Key": str(uuid4())},
    )

    assert created.status_code == 201
    record = created.get_json()
    assert record["id"] == "AQT-2026-ABCDEF1234"
    assert record["patenteVehiculo"] == "ABC123"
    assert record["correo"] == "cliente@solventa.co"

    found = client.get(f"/v1/solicitudes-cotizacion/{record['id']}")
    assert found.status_code == 200
    assert found.get_json() == record


def test_invalid_request_is_not_sent(app: Flask) -> None:
    client = app.test_client()
    login(client)

    response = client.post(
        "/v1/solicitudes-cotizacion",
        json={**request_payload(), "correo": "invalid"},
        headers={"Idempotency-Key": str(uuid4())},
    )

    assert response.status_code == 400


def test_lists_offers_for_an_authenticated_customer(app: Flask) -> None:
    client = app.test_client()
    login(client)

    response = client.get("/v1/solicitudes-cotizacion/AQT-2026-ABCDEF1234/ofertas")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["solicitudId"] == "AQT-2026-ABCDEF1234"
    assert payload["ofertas"][0]["nombre"] == "Seguro Automotriz Pro"
    assert payload["ofertas"][0]["primaMensual"] == {
        "monto": "1.8000",
        "moneda": "UF",
    }


def test_selects_and_reads_an_offer(app: Flask) -> None:
    client = app.test_client()
    login(client)
    offer_id = uuid4()
    url = "/v1/solicitudes-cotizacion/AQT-2026-ABCDEF1234/seleccion"

    selected = client.put(
        url,
        json={"ofertaId": str(offer_id), "version": 1},
        headers={"Idempotency-Key": str(uuid4())},
    )
    found = client.get(url)

    assert selected.status_code == 200
    assert found.status_code == 200
    assert selected.get_json() == found.get_json()
    assert selected.get_json()["ofertaId"] == str(offer_id)


def test_offer_routes_require_authentication(app: Flask) -> None:
    client = app.test_client()
    request_id = "AQT-2026-ABCDEF1234"

    assert client.get(f"/v1/solicitudes-cotizacion/{request_id}/ofertas").status_code == 401
    assert client.get(f"/v1/solicitudes-cotizacion/{request_id}/seleccion").status_code == 401
