from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
from bff_web.domain.auth import User
from bff_web.domain.mortgage_profiling import MortgageProfilingError
from bff_web.domain.partner_offer import PartnerOffer as PartnerOfferSnapshot
from bff_web.domain.partner_operation import PartnerOffer as PartnerOperationOffer
from bff_web.domain.partner_operation import PartnerOperation
from bff_web.domain.quotes import InsuranceProduct
from flask import Flask
from support import USER, FakeIdentity, KeyPair, partner_offer_payload, sign_token

PARTNER = User(uuid4(), "Operador Banco", "operador@banco.co", "socio", partner_ids=("BANCO-001",))
PATH = "/v1/socios/solicitudes-cotizacion"


def payload() -> dict[str, Any]:
    return {
        "operationId": str(uuid4()),
        "socioId": "BANCO-001",
        "productoId": "mortgage-life",
        "datosProducto": {
            "mortgageBalance": "180000000",
            "referenciaCredito": "HIP-2026-001",
        },
        "cliente": {
            "tipoCliente": "persona_natural",
            "tipoDocumento": "cc",
            "numeroDocumento": "1234567890",
        },
    }


@pytest.fixture(autouse=True)
def valid_consent(app: Flask) -> Mock:
    profiling = Mock()
    profiling.get_consent.return_value = {
        "consentimientoId": str(uuid4()),
        "clienteId": str(USER.user_id),
        "version": 2,
        "estado": "autorizado",
        "finalidad": "cotizacion",
        "alcances": ["open_finance", "open_data"],
        "efectivaEn": "2026-10-25T23:00:00Z",
        "vigenteHasta": "2099-10-25T23:00:00Z",
        "actorId": str(USER.user_id),
        "fuentes": ["open_finance", "open_data"],
        "datos": ["indicador_riesgo"],
        "versionTerminos": "1.0",
    }
    app.extensions["profiling_gateway"] = profiling
    return profiling


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
            oferta=PartnerOperationOffer.model_validate(
                {
                    "id": str(uuid4()),
                    "version": 1,
                    "nombre": "Plan recomendado",
                    "descripcion": "Proteccion para el saldo hipotecario.",
                    "prima": {"monto": "99000.00", "moneda": "COP"},
                    "periodicidad": "mensual",
                    "primaMensual": {"monto": "99000.00", "moneda": "COP"},
                    "coberturaPrincipal": "Saldo hipotecario",
                    "deducible": "Sin deducible",
                    "vigencia": "12 meses",
                    "responsabilidadCivil": "No aplica",
                    "coberturas": ["Fallecimiento"],
                    "condiciones": ["Consentimiento vigente"],
                    "beneficiosAdicionales": [],
                    "recomendada": True,
                    "estado": "disponible",
                    "vigenteHasta": "2099-10-25T23:00:00Z",
                    "explicacion": {
                        "resumen": "La prima protege el saldo hipotecario.",
                        "factoresDeterminantes": [
                            {
                                "nombre": "Saldo hipotecario",
                                "valor": "$180.000.000 COP",
                                "impacto": "Define la suma asegurada.",
                            }
                        ],
                    },
                    "trazabilidad": {
                        "esquemaDecision": 1,
                        "hashDecision": "a" * 64,
                        "hashContenido": "b" * 64,
                        "perfilId": str(uuid4()),
                        "perfilVersion": 1,
                        "modeloPerfilVersion": "perfil-v1",
                        "reglaId": str(uuid4()),
                        "reglaVersion": "academico-v1",
                        "fuentes": [
                            {"nombre": "open_finance", "version": "v1"},
                            {"nombre": "open_data", "version": "v1"},
                        ],
                    },
                }
            ),
        )


def headers(keypair: KeyPair, body: dict[str, Any], **claims: Any) -> dict[str, str]:
    values = {"scope": "socio", "socios": ["BANCO-001"], **claims}
    return {
        "Authorization": "Bearer " + sign_token(keypair, user=PARTNER, **values),
        "Idempotency-Key": body["operationId"],
        "API-Version": "1",
    }


def test_real_partner_context_and_authoritative_client(
    app: Flask,
    keypair: KeyPair,
    identity: FakeIdentity,
) -> None:
    gateway = PartnerGateway()
    app.extensions["partner_quote_gateway"] = gateway
    body = payload()
    response = app.test_client().post(PATH, json=body, headers=headers(keypair, body))
    assert response.status_code == 201
    assert response.get_json()["productoId"] == "mortgage-life"
    assert response.get_json()["oferta"]["explicacion"]["factoresDeterminantes"]
    assert response.get_json()["oferta"]["trazabilidad"]["reglaVersion"] == "academico-v1"
    assert response.headers["Cache-Control"] == "no-store"
    assert gateway.body is not None
    assert gateway.body["usuarioId"] == str(PARTNER.user_id)
    assert gateway.body["correo"] == USER.email
    assert gateway.body["telefono"] == identity.clients[0].phone
    assert gateway.body["datosProducto"] == {
        "birthDate": "1985-02-10",
        "mortgageBalance": "180000000",
    }
    assert gateway.body["referenciaCredito"] == "HIP-2026-001"
    assert identity.calls[-1][0] == "get_client_by_document"
    assert gateway.body["consentimientoVersion"] == 2

    quote_gateway = Mock()
    quote_gateway.list_products.return_value = (
        InsuranceProduct(
            "mortgage-life",
            "Seguro de Vida Hipotecario",
            "Protección para el crédito de vivienda",
            True,
            ("Saldo hipotecario",),
        ),
    )
    app.extensions["quote_gateway"] = quote_gateway
    current = app.test_client().get("/v1/socios/actual", headers=headers(keypair, body))
    assert current.status_code == 200
    assert current.get_json()["usuario"]["rol"] == "socio"
    assert current.get_json()["usuario"]["socios"] == ["BANCO-001"]
    assert current.get_json()["socios"] == [
        {"socioId": "BANCO-001", "productos": ["mortgage-life"]}
    ]
    assert current.get_json()["alcances"] == ["perfilamientos:crear", "clientes:consultar"]


def test_partner_finds_client_and_only_receives_useful_consent_status(
    app: Flask, keypair: KeyPair
) -> None:
    response = app.test_client().get(
        "/v1/socios/clientes/por-documento",
        query_string={
            "socioId": "BANCO-001",
            "tipoDocumento": "cc",
            "numeroDocumento": "1234567890",
        },
        headers={
            "Authorization": "Bearer "
            + sign_token(
                keypair,
                user=PARTNER,
                scope="socio",
                socios=["BANCO-001"],
            ),
            "API-Version": "1",
        },
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "nombre": USER.name,
        "tipoDocumento": "cc",
        "numeroDocumentoEnmascarado": "******7890",
        "consentimientoEstado": "vigente",
    }
    assert "clienteId" not in response.get_json()
    assert "version" not in response.get_json()


def test_partial_consent_is_not_reported_as_current_for_mortgage_risk(
    app: Flask, keypair: KeyPair, valid_consent: Mock
) -> None:
    valid_consent.get_consent.return_value["alcances"] = ["open_data"]
    valid_consent.get_consent.return_value["fuentes"] = ["open_data"]

    response = app.test_client().get(
        "/v1/socios/clientes/por-documento",
        query_string={
            "socioId": "BANCO-001",
            "tipoDocumento": "cc",
            "numeroDocumento": "1234567890",
        },
        headers={
            "Authorization": "Bearer "
            + sign_token(keypair, user=PARTNER, scope="socio", socios=["BANCO-001"]),
            "API-Version": "1",
        },
    )

    assert response.status_code == 200
    assert response.get_json()["consentimientoEstado"] == "insuficiente"


@pytest.mark.parametrize(
    ("change", "header_change", "claims", "expected"),
    [
        ({"productoId": "automotive"}, {}, {}, 400),
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

    def calculate_offer(self, socio_id: str, operation_id: UUID, _: str) -> PartnerOfferSnapshot:
        return self._offer("calculate", socio_id, operation_id)

    def get_offer(self, socio_id: str, operation_id: UUID, _: str) -> PartnerOfferSnapshot:
        return self._offer("get", socio_id, operation_id)

    def _offer(self, action: str, socio_id: str, operation_id: UUID) -> PartnerOfferSnapshot:
        self.calls.append((action, socio_id, operation_id))
        if self.error:
            raise self.error
        return PartnerOfferSnapshot.model_validate(partner_offer_payload(operation_id, socio_id))


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
