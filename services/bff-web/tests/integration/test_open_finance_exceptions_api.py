from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

from bff_web.domain.auth import User
from bff_web.domain.open_finance_exceptions import OpenFinanceExceptionError
from flask import Flask
from support import USER, KeyPair, sign_token

ADVISOR = User(
    UUID("4323bf51-1899-4a3e-9547-b8ccb9c2c84f"),
    "Laura Asesora",
    "laura@banco.co",
    "asesor",
    partner_ids=("BANCO-001",),
)


@dataclass
class OpenFinanceExceptionGatewayStub:
    record: dict[str, object] = field(default_factory=dict)
    calls: list[tuple[str, tuple[object, ...]]] = field(default_factory=list)
    error: OpenFinanceExceptionError | None = None

    def list_pending(self, partner_id: str, correlation_id: str) -> dict[str, object]:
        self.calls.append(("list", (partner_id, correlation_id)))
        self._raise_error()
        return {"excepciones": [self.record]}

    def get(self, exception_id: UUID, partner_id: str, correlation_id: str) -> dict[str, object]:
        self.calls.append(("get", (exception_id, partner_id, correlation_id)))
        self._raise_error()
        return self.record

    def resolve(
        self,
        exception_id: UUID,
        partner_id: str,
        actor_id: UUID,
        decision: str,
        reason: str,
        correlation_id: str,
    ) -> dict[str, object]:
        self.calls.append(
            ("resolve", (exception_id, partner_id, actor_id, decision, reason, correlation_id))
        )
        self._raise_error()
        return {
            **self.record,
            "estado": "aprobada" if decision == "aprobar" else "rechazada",
            "decision": decision,
            "actorId": str(actor_id),
            "motivo": reason,
        }

    def _raise_error(self) -> None:
        if self.error is not None:
            raise self.error


def _advisor_token(keypair: KeyPair, **overrides: Any) -> str:
    claims: dict[str, Any] = {"socios": ["BANCO-001"]}
    claims.update(overrides)
    return sign_token(
        keypair,
        user=ADVISOR,
        scope="asesor",
        **claims,
    )


def _exception() -> dict[str, object]:
    return {
        "excepcionId": str(uuid4()),
        "operationId": str(uuid4()),
        "socioId": "BANCO-001",
        "clienteId": str(USER.user_id),
        "codigo": "fuente_no_disponible",
        "motivoBloqueo": "La fuente no se pudo consultar.",
        "alcance": "open_finance",
        "finalidad": "cotizacion",
        "datos": ["indicador_riesgo"],
        "consentimiento": {
            "id": str(uuid4()),
            "version": 1,
            "estado": "autorizado",
            "fuentes": ["open_finance", "open_data"],
            "datos": ["indicador_riesgo"],
            "finalidad": "cotizacion",
            "vigenteHasta": "2026-10-17T00:00:00Z",
        },
        "estado": "pendiente",
        "estadoAnterior": "procesamiento_automatico_bloqueado",
        "creadoEn": "2026-10-16T00:00:00Z",
        "correlationId": "origin",
        "trazabilidad": {
            "estadoAnterior": "procesamiento_automatico_bloqueado",
            "estadoActual": "pendiente",
            "alcance": "open_finance",
            "creadoEn": "2026-10-16T00:00:00Z",
            "correlationId": "origin",
        },
    }


def test_advisor_can_list_get_and_resolve_for_authorized_partner(
    app: Flask, keypair: KeyPair
) -> None:
    gateway = OpenFinanceExceptionGatewayStub(record=_exception())
    app.extensions["open_finance_exception_gateway"] = gateway
    client = app.test_client()
    token = _advisor_token(keypair)
    headers = {"Authorization": f"Bearer {token}"}

    listed = client.get(
        "/v1/asesor/excepciones-open-finance?socioId=BANCO-001",
        headers=headers,
    )
    raw_exception_id = gateway.record["excepcionId"]
    assert isinstance(raw_exception_id, str)
    exception_id = UUID(raw_exception_id)
    detail = client.get(
        f"/v1/asesor/excepciones-open-finance/{exception_id}?socioId=BANCO-001",
        headers=headers,
    )
    resolved = client.post(
        f"/v1/asesor/excepciones-open-finance/{exception_id}/resolver?socioId=BANCO-001",
        json={"decision": "aprobar", "motivo": "Revision humana completada"},
        headers=headers,
    )

    assert listed.status_code == 200
    assert listed.get_json() == {"excepciones": [gateway.record]}
    assert detail.status_code == 200
    assert detail.get_json()["consentimiento"]["version"] == 1
    assert resolved.status_code == 200
    assert resolved.get_json()["estado"] == "aprobada"
    assert gateway.calls[-1][1][2] == ADVISOR.user_id


def test_advisor_route_enforces_scope_partner_and_resolution_payload(
    app: Flask, keypair: KeyPair
) -> None:
    gateway = OpenFinanceExceptionGatewayStub(record=_exception())
    app.extensions["open_finance_exception_gateway"] = gateway
    client = app.test_client()
    raw_exception_id = gateway.record["excepcionId"]
    assert isinstance(raw_exception_id, str)
    exception_id = UUID(raw_exception_id)
    route = f"/v1/asesor/excepciones-open-finance/{exception_id}/resolver?socioId="

    assert (
        client.get(
            "/v1/asesor/excepciones-open-finance?socioId=BANCO-001",
            headers={"Authorization": f"Bearer {_advisor_token(keypair, socios=[])}"},
        ).status_code
        == 401
    )
    assert (
        client.get(
            "/v1/asesor/excepciones-open-finance?socioId=BANCO-001",
            headers={"Authorization": f"Bearer {_advisor_token(keypair, socios=['BANCO-999'])}"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            route + "BANCO-001",
            json={"decision": "aprobar", "motivo": "  "},
            headers={"Authorization": f"Bearer {_advisor_token(keypair)}"},
        ).status_code
        == 400
    )


def test_consent_error_is_returned_without_confirming_resolution(
    app: Flask, keypair: KeyPair
) -> None:
    gateway = OpenFinanceExceptionGatewayStub(
        record=_exception(),
        error=OpenFinanceExceptionError("consentimiento_no_vigente", 403),
    )
    app.extensions["open_finance_exception_gateway"] = gateway
    raw_exception_id = gateway.record["excepcionId"]
    assert isinstance(raw_exception_id, str)
    exception_id = UUID(raw_exception_id)

    response = app.test_client().post(
        f"/v1/asesor/excepciones-open-finance/{exception_id}/resolver?socioId=BANCO-001",
        json={"decision": "aprobar", "motivo": "Consentimiento no vigente"},
        headers={"Authorization": f"Bearer {_advisor_token(keypair)}"},
    )

    assert response.status_code == 403
    assert response.get_json()["code"] == "consentimiento_no_vigente"
