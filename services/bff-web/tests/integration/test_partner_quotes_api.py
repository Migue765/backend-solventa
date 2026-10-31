from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from bff_web.domain.auth import User
from bff_web.domain.mortgage_profiling import MortgageProfilingError
from bff_web.domain.partner_offer import PartnerOffer
from bff_web.domain.partner_operation import PartnerOperation
from flask import Flask
from support import USER, FakeIdentity, KeyPair, partner_offer_payload, sign_token

PARTNER = User(uuid4(), "Operador Banco", "operador@banco.co", "socio", partner_ids=("BANCO-001",))
PATH = "/v1/socios/solicitudes-cotizacion"


def payload() -> dict[str, Any]:
    return {
        "operationId": str(uuid4()),
        "socioId": "BANCO-001",
        "productoId": "automotive",
        "datosProducto": {"vehiclePlate": "ABC123"},
        "cliente": {
            "clienteId": str(USER.user_id),
            "tipoCliente": "persona_natural",
            "tipoDocumento": "cc",
            "numeroDocumento": "1023456789",
            "telefono": "+573001234567",
        },
        "consentimientoId": str(uuid4()),
        "consentimientoVersion": 1,
    }


@dataclass
class PartnerGateway:
    body: dict[str, object] | None = None
    error: MortgageProfilingError | None = None

    def start(
        self, body: dict[str, object], operation_id: UUID, correlation_id: str
    ) -> PartnerOperation:
        if self.error:
            raise self.error
        self.body = body
        return PartnerOperation(
            operationId=operation_id,
            socioId=str(body["socioId"]),
            clienteId=UUID(str(body["clienteId"])),
            productoId=str(body["productoId"]),
            estado="completada",
            perfilamientoReferencia=str(uuid4()),
            solicitudId="AQT-2026-123",
            cotizacionId=uuid4(),
            recibidaEn=datetime.now(UTC),
        )


def headers(keypair: KeyPair, body: dict[str, Any], **claims: Any) -> dict[str, str]:
    values = {"scope": "socio", "socios": ["BANCO-001"], **claims}
    return {
        "Authorization": "Bearer " + sign_token(keypair, user=PARTNER, **values),
        "Idempotency-Key": body["operationId"],
        "API-Version": "1",
    }


def test_real_partner_context_and_authoritative_client(
    app: Flask, keypair: KeyPair, identity: FakeIdentity
) -> None:
    gateway = PartnerGateway()
    app.extensions["partner_quote_gateway"] = gateway
    body = payload()
    response = app.test_client().post(PATH, json=body, headers=headers(keypair, body))
    assert response.status_code == 201
    assert response.get_json()["productoId"] == "automotive"
    assert response.headers["Cache-Control"] == "no-store"
    assert gateway.body is not None
    assert gateway.body["usuarioId"] == str(PARTNER.user_id)
    assert gateway.body["correo"] == USER.email
    assert identity.calls[-1][0] == "get_client"

    current = app.test_client().get("/v1/socios/actual", headers=headers(keypair, body))
    assert current.status_code == 200
    assert current.get_json()["usuario"]["rol"] == "socio"
    assert current.get_json()["usuario"]["socios"] == ["BANCO-001"]
    assert current.get_json()["alcances"] == ["perfilamientos:crear", "clientes:consultar"]


@pytest.mark.parametrize(
    ("change", "header_change", "claims", "expected"),
    [
        ({"productoId": "unknown"}, {}, {}, 400),
        ({"productoId": None}, {}, {}, 400),
        ({"datosProducto": {}}, {}, {}, 400),
        ({}, {"API-Version": "2"}, {}, 400),
        ({}, {"Idempotency-Key": str(uuid4())}, {}, 400),
        ({}, {}, {"scope": "cliente"}, 403),
        ({}, {}, {"socios": []}, 403),
        ({"socioId": "OTHER"}, {}, {}, 403),
        ({}, {"Authorization": ""}, {}, 401),
    ],
)
def test_rejections_happen_before_dependencies(
    app: Flask,
    keypair: KeyPair,
    identity: FakeIdentity,
    change: dict[str, Any],
    header_change: dict[str, str],
    claims: dict[str, Any],
    expected: int,
) -> None:
    gateway = PartnerGateway()
    app.extensions["partner_quote_gateway"] = gateway
    body = {**payload(), **change}
    response = app.test_client().post(
        PATH, json=body, headers={**headers(keypair, body, **claims), **header_change}
    )
    assert response.status_code == expected
    assert gateway.body is None
    assert identity.calls == []


@pytest.mark.parametrize(
    "code,status", [("cuota_socio_excedida", 429), ("operacion_en_curso", 409)]
)
def test_preserves_controlled_error_and_retry(
    app: Flask, keypair: KeyPair, code: str, status: int
) -> None:
    app.extensions["partner_quote_gateway"] = PartnerGateway(
        error=MortgageProfilingError(code, status, 12)
    )
    body = payload()
    response = app.test_client().post(PATH, json=body, headers=headers(keypair, body))
    assert response.status_code == status
    assert response.get_json()["code"] == code
    assert response.headers["Retry-After"] == "12"


def test_client_and_advisor_cannot_enter_partner_portal(app: Flask, keypair: KeyPair) -> None:
    for user, scope, partners in ((USER, "cliente", []), (PARTNER, "asesor", ["BANCO-001"])):
        token = sign_token(keypair, user=user, scope=scope, socios=partners)
        response = app.test_client().get(
            "/v1/socios/actual", headers={"Authorization": "Bearer " + token}
        )
        assert response.status_code == 403


@dataclass
class OfferGateway:
    error: MortgageProfilingError | None = None
    calls: list[tuple[str, str, UUID]] = field(default_factory=list)

    def calculate_offer(self, socio_id: str, operation_id: UUID, _: str) -> PartnerOffer:
        return self._offer("calculate", socio_id, operation_id)

    def get_offer(self, socio_id: str, operation_id: UUID, _: str) -> PartnerOffer:
        return self._offer("get", socio_id, operation_id)

    def _offer(self, action: str, socio_id: str, operation_id: UUID) -> PartnerOffer:
        self.calls.append((action, socio_id, operation_id))
        if self.error:
            raise self.error
        return PartnerOffer.model_validate(partner_offer_payload(operation_id, socio_id))


def offer_url(operation_id: UUID, socio: str = "BANCO-001") -> str:
    return f"{PATH}/{operation_id}/oferta?socioId={socio}"


def test_partner_calculates_and_recovers_offer(app: Flask, keypair: KeyPair) -> None:
    gateway = OfferGateway()
    app.extensions["partner_quote_gateway"] = gateway
    operation_id = uuid4()
    auth = headers(keypair, payload())

    created = app.test_client().post(offer_url(operation_id), headers=auth)
    recovered = app.test_client().get(offer_url(operation_id), headers=auth)

    assert created.status_code == 201
    assert created.headers["Cache-Control"] == "no-store"
    assert created.get_json()["ofertas"][0]["factores"][0]["codigo"] == "ajuste_riesgo"
    assert recovered.status_code == 200
    assert recovered.get_json()["operationId"] == str(operation_id)
    assert gateway.calls == [
        ("calculate", "BANCO-001", operation_id),
        ("get", "BANCO-001", operation_id),
    ]


@pytest.mark.parametrize(
    ("socio", "header_change", "claims", "expected"),
    [
        ("OTHER", {}, {}, 403),
        ("", {}, {}, 403),
        ("BANCO-001", {"API-Version": "2"}, {}, 400),
        ("BANCO-001", {}, {"scope": "cliente"}, 403),
        ("BANCO-001", {"Authorization": ""}, {}, 401),
    ],
)
def test_offer_rejections_happen_before_cotizacion(
    app: Flask,
    keypair: KeyPair,
    socio: str,
    header_change: dict[str, str],
    claims: dict[str, Any],
    expected: int,
) -> None:
    gateway = OfferGateway()
    app.extensions["partner_quote_gateway"] = gateway
    auth = {**headers(keypair, payload(), **claims), **header_change}

    response = app.test_client().post(offer_url(uuid4(), socio), headers=auth)

    assert response.status_code == expected
    assert gateway.calls == []


@pytest.mark.parametrize(
    ("code", "status", "public", "public_status"),
    [
        ("cotizacion_vencida", 422, "entradas_vencidas", 422),
        ("perfil_vencido", 422, "entradas_vencidas", 422),
        ("fuente_vencida", 422, "entradas_vencidas", 422),
        ("decision_inconsistente", 422, "oferta_no_disponible", 422),
        ("oferta_no_encontrada", 404, "oferta_no_encontrada", 404),
        ("perfilamiento_no_disponible", 503, "servicio_no_disponible", 503),
    ],
)
def test_offer_errors_never_return_partial_offers(
    app: Flask, keypair: KeyPair, code: str, status: int, public: str, public_status: int
) -> None:
    app.extensions["partner_quote_gateway"] = OfferGateway(
        error=MortgageProfilingError(code, status)
    )

    response = app.test_client().post(offer_url(uuid4()), headers=headers(keypair, payload()))

    assert response.status_code == public_status
    assert response.get_json()["code"] == public
    assert "ofertas" not in response.get_json()
