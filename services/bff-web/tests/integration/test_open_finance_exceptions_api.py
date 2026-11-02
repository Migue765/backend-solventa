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
    apply_error: OpenFinanceExceptionError | None = None

    def list_pending(
        self, partner_id: str, correlation_id: str, application_state: str | None = None
    ) -> dict[str, object]:
        self.calls.append(("list", (partner_id, correlation_id)))
        if application_state is not None:
            self.calls.append(("filter", (application_state,)))
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
        resolved = {
            **self.record,
            "estado": "aprobada" if decision == "aprobar" else "rechazada",
            "decision": decision,
            "actorId": str(actor_id),
            "motivo": reason,
        }
        if self.record.get("proceso") is not None:
            self.record = {
                **resolved,
                "aplicacion": {
                    "estado": "pendiente",
                    "intentos": 0,
                    "codigo": None,
                    "actualizadaEn": "2026-11-01T22:00:00Z",
                    "acuseId": None,
                },
            }
            return self.record
        return resolved

    def apply(
        self,
        exception_id: UUID,
        partner_id: str,
        actor_id: UUID,
        correlation_id: str,
        impediment: str | None = None,
    ) -> dict[str, object]:
        self.calls.append(("apply", (exception_id, partner_id, actor_id, impediment)))
        if self.apply_error is not None:
            raise self.apply_error
        previous = self.record["aplicacion"]
        assert isinstance(previous, dict)
        if previous["acuseId"] is None:
            rejected = self.record["estado"] == "rechazada"
            self.record["aplicacion"] = {
                "estado": "detenida" if rejected else "bloqueada" if impediment else "completada",
                "intentos": 0 if rejected else 1,
                "codigo": "operacion_rechazada"
                if rejected
                else impediment or "consulta_disponible",
                "actualizadaEn": "2026-11-01T22:00:01Z",
                "acuseId": str(uuid4()),
            }
        return self.record

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


def _linked_exception(product_id: str = "automotive") -> dict[str, object]:
    return {
        **_exception(),
        "proceso": {
            "tipo": "consulta_informacion_cliente",
            "actorSolicitanteId": str(uuid4()),
            "productoId": product_id,
            "finalidad": "cotizacion",
            "datosSolicitados": ["indicador_riesgo"],
        },
        "aplicacion": {
            "estado": "esperando_decision",
            "intentos": 0,
            "codigo": None,
            "actualizadaEn": "2026-11-01T21:59:00Z",
            "acuseId": None,
        },
    }


def test_linked_resolution_dispatches_and_keeps_decision_distinct_from_ack(
    app: Flask, keypair: KeyPair
) -> None:
    gateway = OpenFinanceExceptionGatewayStub(record=_linked_exception())
    app.extensions["open_finance_exception_gateway"] = gateway
    response = app.test_client().post(
        f"/v1/asesor/excepciones-open-finance/{gateway.record['excepcionId']}/resolver?socioId=BANCO-001",
        headers={"Authorization": f"Bearer {_advisor_token(keypair)}"},
        json={"decision": "aprobar", "motivo": "Revision con reintento acotado"},
    )
    assert response.status_code == 200
    assert response.get_json()["estado"] == "aprobada"
    assert response.get_json()["aplicacion"]["estado"] == "completada"
    assert response.get_json()["aplicacion"]["acuseId"] is not None
    assert [call[0] for call in gateway.calls] == ["resolve", "apply"]


def test_unavailable_product_blocks_application_without_undoing_decision(
    app: Flask, keypair: KeyPair
) -> None:
    gateway = OpenFinanceExceptionGatewayStub(record=_linked_exception("removed-product"))
    app.extensions["open_finance_exception_gateway"] = gateway
    response = app.test_client().post(
        f"/v1/asesor/excepciones-open-finance/{gateway.record['excepcionId']}/resolver?socioId=BANCO-001",
        headers={"Authorization": f"Bearer {_advisor_token(keypair)}"},
        json={"decision": "aprobar", "motivo": "Revision con producto retirado"},
    )
    assert response.status_code == 200
    assert response.get_json()["estado"] == "aprobada"
    assert response.get_json()["aplicacion"]["estado"] == "bloqueada"
    assert response.get_json()["aplicacion"]["codigo"] == "producto_no_autorizado"


def test_interrupted_dispatch_remains_discoverable_and_can_be_redelivered(
    app: Flask, keypair: KeyPair
) -> None:
    gateway = OpenFinanceExceptionGatewayStub(
        record=_linked_exception(),
        apply_error=OpenFinanceExceptionError("servicio_no_disponible", 503),
    )
    app.extensions["open_finance_exception_gateway"] = gateway
    client = app.test_client()
    headers = {"Authorization": f"Bearer {_advisor_token(keypair)}"}
    root = f"/v1/asesor/excepciones-open-finance/{gateway.record['excepcionId']}"
    assert (
        client.post(
            root + "/resolver?socioId=BANCO-001",
            headers=headers,
            json={"decision": "aprobar", "motivo": "Decision durable antes del fallo"},
        ).status_code
        == 503
    )
    listed = client.get(
        "/v1/asesor/excepciones-open-finance?socioId=BANCO-001&estadoAplicacion=pendiente",
        headers=headers,
    )
    assert listed.status_code == 200
    assert listed.get_json()["excepciones"][0]["estado"] == "aprobada"
    assert listed.get_json()["excepciones"][0]["aplicacion"]["acuseId"] is None
    gateway.apply_error = None
    recovered = client.post(root + "/aplicar?socioId=BANCO-001", json={}, headers=headers)
    assert recovered.status_code == 200
    assert recovered.get_json()["aplicacion"]["estado"] == "completada"
    assert [call[0] for call in gateway.calls].count("resolve") == 1
    assert (
        client.post(root + "/aplicar?socioId=BANCO-001", json={}, headers=headers).get_json()[
            "aplicacion"
        ]
        == recovered.get_json()["aplicacion"]
    )


def test_application_guards_payload_scope_history_and_decision(
    app: Flask, keypair: KeyPair
) -> None:
    gateway = OpenFinanceExceptionGatewayStub(record=_exception())
    app.extensions["open_finance_exception_gateway"] = gateway
    client = app.test_client()
    headers = {"Authorization": f"Bearer {_advisor_token(keypair)}"}
    route = f"/v1/asesor/excepciones-open-finance/{gateway.record['excepcionId']}/aplicar"
    assert client.post(route + "?socioId=BANCO-001", json={}).status_code == 401
    assert client.post(route + "?socioId=BANCO-999", json={}, headers=headers).status_code == 403
    assert (
        client.post(
            route + "?socioId=BANCO-001", json={"impedimento": None}, headers=headers
        ).status_code
        == 400
    )
    assert (
        client.post(route + "?socioId=BANCO-001", json={}, headers=headers).get_json()["code"]
        == "excepcion_sin_operacion"
    )
    gateway.record = {**_linked_exception(), "excepcionId": gateway.record["excepcionId"]}
    assert (
        client.post(route + "?socioId=BANCO-001", json={}, headers=headers).get_json()["code"]
        == "decision_pendiente"
    )
    assert not any(call[0] == "apply" for call in gateway.calls)
