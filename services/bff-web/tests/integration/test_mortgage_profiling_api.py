from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from bff_web.domain.auth import User
from bff_web.domain.mortgage_profiling import (
    MortgageProfilingCommand,
    MortgageProfilingError,
    MortgageProfilingResult,
)
from flask import Flask
from support import USER, FakeIdentity, KeyPair, sign_token

ADVISOR = User(
    UUID("4323bf51-1899-4a3e-9547-b8ccb9c2c84f"),
    "Laura Asesora",
    "laura@banco.co",
    "asesor",
    partner_ids=("BANCO-001",),
)


@dataclass
class MortgageGatewayStub:
    command: MortgageProfilingCommand | None = None
    error: MortgageProfilingError | None = None

    def start(
        self, command: MortgageProfilingCommand, correlation_id: str
    ) -> MortgageProfilingResult:
        if self.error is not None:
            raise self.error
        self.command = command
        return MortgageProfilingResult(
            operation_id=command.operation_id,
            partner_id=command.partner_id,
            advisor_id=command.advisor_id,
            client_id=command.client.client_id,
            status="completada",
            profiling_reference=str(uuid4()),
            request_id="AQT-2026-ABC123",
            quote_id=uuid4(),
            received_at=datetime(2026, 10, 20, tzinfo=UTC),
        )


def _payload(operation_id: UUID, **changes: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "operationId": str(operation_id),
        "socioId": "BANCO-001",
        "cliente": {
            "clienteId": str(USER.user_id),
            "tipoDocumento": "cc",
            "numeroDocumento": "1023456789",
            "telefono": "+573001234567",
            "fechaNacimiento": "1985-02-10",
        },
        "credito": {
            "referencia": "HIP-2026-001",
            "monto": 310000000,
            "plazoMeses": 240,
        },
        "consentimientoId": str(uuid4()),
        "consentimientoVersion": 2,
    }
    payload.update(changes)
    return payload


def _advisor_token(keypair: KeyPair) -> str:
    return sign_token(
        keypair,
        user=ADVISOR,
        scope="asesor",
        socios=["BANCO-001"],
    )


def test_advisor_lists_registered_clients(
    app: Flask, identity: FakeIdentity, keypair: KeyPair
) -> None:
    response = app.test_client().get(
        "/v1/asesor/clientes",
        headers={"Authorization": f"Bearer {_advisor_token(keypair)}"},
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "clientes": [
            {
                "clienteId": str(USER.user_id),
                "nombre": USER.name,
                "correo": USER.email,
            }
        ]
    }
    assert identity.calls[-1][0] == "list_clients"


def test_advisor_starts_operation_with_authoritative_context(
    app: Flask, identity: FakeIdentity, keypair: KeyPair
) -> None:
    gateway = MortgageGatewayStub()
    app.extensions["mortgage_profiling_gateway"] = gateway
    operation_id = uuid4()

    response = app.test_client().post(
        "/v1/perfilamientos-hipotecarios",
        json=_payload(operation_id),
        headers={
            "Authorization": f"Bearer {_advisor_token(keypair)}",
            "Idempotency-Key": str(operation_id),
        },
    )

    assert response.status_code == 201
    assert response.get_json()["estado"] == "completada"
    assert gateway.command is not None
    assert gateway.command.advisor_id == ADVISOR.user_id
    assert gateway.command.partner_id == "BANCO-001"
    assert gateway.command.client.client_id == USER.user_id
    assert gateway.command.client.email == USER.email
    assert identity.calls[-1][0] == "list_clients"


def test_rejects_partner_outside_advisor_claims(app: Flask, keypair: KeyPair) -> None:
    operation_id = uuid4()

    response = app.test_client().post(
        "/v1/perfilamientos-hipotecarios",
        json=_payload(operation_id, socioId="BANCO-999"),
        headers={
            "Authorization": f"Bearer {_advisor_token(keypair)}",
            "Idempotency-Key": str(operation_id),
        },
    )

    assert response.status_code == 403
    assert response.get_json()["code"] == "socio_no_autorizado"


def test_client_token_cannot_use_advisor_operation(
    app: Flask,
    token: str,
) -> None:
    operation_id = uuid4()

    response = app.test_client().post(
        "/v1/perfilamientos-hipotecarios",
        json=_payload(operation_id),
        headers={
            "Authorization": f"Bearer {token}",
            "Idempotency-Key": str(operation_id),
        },
    )

    assert response.status_code == 401
