from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
from bff_web.domain.auth import User
from bff_web.domain.mortgage_profiling import MortgageProfilingError
from bff_web.domain.partner_operation import PartnerOperation
from bff_web.domain.quotes import InsuranceProduct
from flask import Flask
from support import USER, FakeIdentity, KeyPair, sign_token

PARTNER = User(uuid4(), "Operador Banco", "operador@banco.co", "socio", partner_ids=("BANCO-001",))
PATH = "/v1/socios/solicitudes-cotizacion"


def payload() -> dict[str, Any]:
    return {
        "operationId": str(uuid4()),
        "socioId": "BANCO-001",
        "productoId": "mortgage-life",
        "datosProducto": {"birthDate": "1985-02-10", "mortgageBalance": "180000000"},
        "cliente": {
            "tipoCliente": "persona_natural",
            "tipoDocumento": "cc",
            "numeroDocumento": "1234567890",
            "telefono": "+573001234567",
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
    assert response.headers["Cache-Control"] == "no-store"
    assert gateway.body is not None
    assert gateway.body["usuarioId"] == str(PARTNER.user_id)
    assert gateway.body["correo"] == USER.email
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
