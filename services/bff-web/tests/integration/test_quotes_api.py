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
    TravelDestination,
)
from bff_web.infrastructure.development_credentials import DevelopmentCredential
from bff_web.infrastructure.passwords import hash_password
from flask import Flask
from flask.testing import FlaskClient

PASSWORD = "Solventa123!"
DATOS_PRODUCTO = {
    "automotive": {"vehiclePlate": "ABC123"},
    "travel": {
        "destinationCountryCode": "ES",
        "departureDate": "2026-11-01",
        "returnDate": "2026-11-08",
    },
    "life": {"birthDate": "1990-06-15"},
    "device-protection": {
        "deviceType": "Teléfono",
        "brandModel": "Acme Pro 12",
        "purchaseValue": "4500000",
    },
    "parametric": {
        "airline": "Aerolínea Colombiana",
        "flightNumber": "AV123",
        "flightDate": "2026-11-20",
    },
    "payment-protection": {
        "creditType": "Crédito de libre inversión",
        "monthlyPayment": "850000",
    },
    "mortgage-life": {"birthDate": "1985-02-10", "mortgageBalance": "180000000"},
}
DATOS_INVALIDOS = {
    "automotive": {"vehiclePlate": "abc123"},
    "travel": {
        "destinationCountryCode": "ES",
        "departureDate": "2026-11-08",
        "returnDate": "2026-11-01",
    },
    "life": {"birthDate": "1990-13-15"},
    "device-protection": {
        "deviceType": "",
        "brandModel": "Acme Pro 12",
        "purchaseValue": "4500000",
    },
    "parametric": {
        "airline": "Aerolínea Colombiana",
        "flightNumber": "A",
        "flightDate": "2026-11-20",
    },
    "payment-protection": {
        "creditType": "Crédito de libre inversión",
        "monthlyPayment": "850000.50",
    },
    "mortgage-life": {"birthDate": "1985-02-30", "mortgageBalance": "180000000"},
}


class StubQuoteGateway(QuoteGateway):
    def __init__(self) -> None:
        self.record: QuoteRequestRecord | None = None
        self.selection: OfferSelection | None = None

    def list_products(self) -> tuple[InsuranceProduct, ...]:
        return tuple(
            InsuranceProduct(
                product_id,
                product_id,
                "Protección para el vehículo",
                True,
            )
            for product_id in DATOS_PRODUCTO
        )

    def list_travel_destinations(self) -> tuple[TravelDestination, ...]:
        return (
            TravelDestination("CO", "Colombia"),
            TravelDestination("ES", "España"),
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


def request_payload(
    product_id: str = "automotive",
    product_data: dict[str, str] | None = None,
) -> dict[str, object]:
    return {
        "productoId": product_id,
        "datosProducto": DATOS_PRODUCTO[product_id] if product_data is None else product_data,
    }


def test_catalog_requires_authentication(app: Flask) -> None:
    assert app.test_client().get("/v1/productos-seguro").status_code == 401


def test_lists_every_available_product(app: Flask) -> None:
    client = app.test_client()
    login(client)

    response = client.get("/v1/productos-seguro")

    assert response.status_code == 200
    products = response.get_json()["productos"]
    assert [product["id"] for product in products] == list(DATOS_PRODUCTO)
    assert all(product["disponible"] is True for product in products)


def test_lists_travel_destinations_for_an_authenticated_user(app: Flask) -> None:
    client = app.test_client()
    login(client)

    response = client.get("/v1/destinos-viaje")

    assert response.status_code == 200
    assert response.get_json() == {
        "destinos": [
            {"codigo": "CO", "nombre": "Colombia"},
            {"codigo": "ES", "nombre": "España"},
        ]
    }


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
    assert record["datosProducto"] == {"vehiclePlate": "ABC123"}
    assert record["correo"] == "cliente@solventa.co"
    assert record["tipoDocumento"] == "cc"
    assert record["identificacion"] == "1234567890"

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


@pytest.mark.parametrize("product_id", DATOS_PRODUCTO)
def test_accepts_product_data_for_every_product(app: Flask, product_id: str) -> None:
    client = app.test_client()
    login(client)

    response = client.post(
        "/v1/solicitudes-cotizacion",
        json=request_payload(product_id),
        headers={"Idempotency-Key": str(uuid4())},
    )

    assert response.status_code == 201
    assert response.get_json()["datosProducto"] == DATOS_PRODUCTO[product_id]


def test_rejects_an_unsupported_travel_destination(app: Flask) -> None:
    client = app.test_client()
    login(client)
    product_data = {
        **DATOS_PRODUCTO["travel"],
        "destinationCountryCode": "ZZ",
    }

    response = client.post(
        "/v1/solicitudes-cotizacion",
        json=request_payload("travel", product_data),
        headers={"Idempotency-Key": str(uuid4())},
    )

    assert response.status_code == 400
    assert response.get_json()["code"] == "solicitud_invalida"


@pytest.mark.parametrize(("product_id", "product_data"), DATOS_INVALIDOS.items())
def test_rejects_invalid_data_for_every_product(
    app: Flask, product_id: str, product_data: dict[str, str]
) -> None:
    client = app.test_client()
    login(client)

    response = client.post(
        "/v1/solicitudes-cotizacion",
        json=request_payload(product_id, product_data),
        headers={"Idempotency-Key": str(uuid4())},
    )

    assert response.status_code == 400


@pytest.mark.parametrize(
    "product_data",
    [{"vehiclePlate": "ABC123", "extra": "no"}, {}],
)
def test_rejects_extra_and_missing_product_data(app: Flask, product_data: dict[str, str]) -> None:
    client = app.test_client()
    login(client)

    response = client.post(
        "/v1/solicitudes-cotizacion",
        json=request_payload(product_data=product_data),
        headers={"Idempotency-Key": str(uuid4())},
    )

    assert response.status_code == 400


def test_rejects_a_phone_outside_the_colombian_market(app: Flask) -> None:
    client = app.test_client()
    login(client)

    response = client.post(
        "/v1/solicitudes-cotizacion",
        json={**request_payload(), "telefono": "+56912345678"},
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
        "monto": "189900",
        "moneda": "COP",
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
