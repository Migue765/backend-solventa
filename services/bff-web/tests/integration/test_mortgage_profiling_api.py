import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from io import BytesIO
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
from bff_web.domain.auth import User
from bff_web.domain.client_information import ClientInformationResult
from bff_web.domain.mortgage_profiling import (
    MortgageClient,
    MortgageCredit,
    MortgageProfilingCommand,
    MortgageProfilingError,
    MortgageProfilingResult,
)
from bff_web.infrastructure.http_mortgage_profiling_gateway import HttpMortgageProfilingGateway
from flask import Flask
from support import USER, FakeIdentity, KeyPair, sign_token

ADVISOR = User(
    UUID("4323bf51-1899-4a3e-9547-b8ccb9c2c84f"),
    "Laura Asesora",
    "laura@banco.co",
    "socio",
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
            "numeroDocumento": USER.identification,
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
        "consultaInformacionId": str(uuid4()),
    }
    payload.update(changes)
    return payload


def _advisor_token(keypair: KeyPair) -> str:
    return sign_token(
        keypair,
        user=ADVISOR,
        scope="socio",
        socios=["BANCO-001"],
    )


def test_no_role_lists_global_clients(app: Flask, identity: FakeIdentity, keypair: KeyPair) -> None:
    response = app.test_client().get(
        "/v1/asesor/clientes",
        headers={"Authorization": f"Bearer {_advisor_token(keypair)}"},
    )

    assert response.status_code == 403
    assert not any(call[0] == "list_clients" for call in identity.calls)


def test_advisor_starts_operation_with_authoritative_context(
    app: Flask, identity: FakeIdentity, keypair: KeyPair
) -> None:
    gateway = MortgageGatewayStub()
    app.extensions["mortgage_profiling_gateway"] = gateway
    operation_id = uuid4()
    payload = _payload(operation_id)
    information = Mock()
    information.recover.return_value = ClientInformationResult.model_validate(
        {
            "operationId": payload["consultaInformacionId"],
            "socioId": "BANCO-001",
            "actorId": str(ADVISOR.user_id),
            "clienteId": str(USER.user_id),
            "productoId": "mortgage-life",
            "finalidad": "cotizacion",
            "estado": "completada",
            "consentimientoId": payload["consentimientoId"],
            "consentimientoVersion": 2,
            "datos": {},
            "evidenciaFuentes": [],
            "consultadaEn": "2026-10-20T00:00:00Z",
        }
    )
    app.extensions["client_information_gateway"] = information

    response = app.test_client().post(
        "/v1/perfilamientos-hipotecarios",
        json=payload,
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
    assert identity.calls[-1][0] == "get_client"


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

    assert response.status_code == 403


@pytest.mark.parametrize(
    "changes",
    [
        {"actorId": str(uuid4())},
        {"clienteId": str(uuid4())},
        {"socioId": "BANCO-999"},
        {"productoId": "automotive"},
        {"consentimientoId": str(uuid4())},
        {"consentimientoVersion": 1},
    ],
)
def test_partner_cannot_originate_from_foreign_or_stale_consented_context(
    app: Flask, identity: FakeIdentity, keypair: KeyPair, changes: dict[str, object]
) -> None:
    operation_id = uuid4()
    payload = _payload(operation_id)
    information = Mock()
    information.recover.return_value = ClientInformationResult.model_validate(
        {
            "operationId": payload["consultaInformacionId"],
            "socioId": "BANCO-001",
            "actorId": str(ADVISOR.user_id),
            "clienteId": str(USER.user_id),
            "productoId": "mortgage-life",
            "finalidad": "cotizacion",
            "estado": "completada",
            "consentimientoId": payload["consentimientoId"],
            "consentimientoVersion": 2,
            "datos": {},
            "evidenciaFuentes": [],
            "consultadaEn": "2026-10-20T00:00:00Z",
            **changes,
        }
    )
    app.extensions["client_information_gateway"] = information
    gateway = MortgageGatewayStub()
    app.extensions["mortgage_profiling_gateway"] = gateway
    response = app.test_client().post(
        "/v1/perfilamientos-hipotecarios",
        json=payload,
        headers={
            "Authorization": "Bearer " + _advisor_token(keypair),
            "Idempotency-Key": str(operation_id),
        },
    )
    assert response.status_code == 403
    assert gateway.command is None
    assert not any(call[0] == "get_client" for call in identity.calls)


def test_operational_advisor_cannot_originate_commercial_mortgage(
    app: Flask, keypair: KeyPair
) -> None:
    operation_id = uuid4()
    token = sign_token(
        keypair, rol="asesor", scope="asesor", socios=[], permisos=["siniestros:revisar"]
    )
    response = app.test_client().post(
        "/v1/perfilamientos-hipotecarios",
        json=_payload(operation_id),
        headers={"Authorization": "Bearer " + token, "Idempotency-Key": str(operation_id)},
    )
    assert response.status_code == 403


@pytest.mark.parametrize("actor_key", ["actorId", "asesorId"])
def test_mortgage_wire_uses_actor_and_recovers_both_response_formats(
    monkeypatch: pytest.MonkeyPatch, actor_key: str
) -> None:
    command = MortgageProfilingCommand(
        operation_id=uuid4(),
        partner_id="BANCO-001",
        advisor_id=ADVISOR.user_id,
        client=MortgageClient(
            client_id=USER.user_id,
            document_type="cc",
            identification=USER.identification,
            email=USER.email,
            phone="+573001234567",
            birth_date=date(1985, 2, 10),
        ),
        credit=MortgageCredit(
            reference="HIP-2026-001", amount=Decimal("310000000"), term_months=240
        ),
        consent_id=uuid4(),
        consent_version=2,
    )
    payload = {
        "operationId": str(command.operation_id),
        "socioId": command.partner_id,
        actor_key: str(command.advisor_id),
        "clienteId": str(command.client.client_id),
        "estado": "completada",
        "perfilamientoReferencia": str(uuid4()),
        "solicitudId": "AQT-TEST",
        "cotizacionId": str(uuid4()),
        "recibidaEn": "2026-10-20T00:00:00Z",
    }
    upstream = Mock(return_value=BytesIO(json.dumps(payload).encode()))
    monkeypatch.setattr("bff_web.infrastructure.http_mortgage_profiling_gateway.urlopen", upstream)
    gateway = HttpMortgageProfilingGateway("http://cotizacion.test", "fixture")
    result = gateway.start(command, "corr-actor")
    sent = json.loads(upstream.call_args.args[0].data)
    assert sent["actorId"] == str(ADVISOR.user_id)
    assert "asesorId" not in sent
    assert result.advisor_id == command.advisor_id
    payload[actor_key] = str(uuid4())
    upstream.return_value = BytesIO(json.dumps(payload).encode())
    with pytest.raises(MortgageProfilingError, match="servicio_no_disponible"):
        gateway.start(command, "corr-wrong-actor")
