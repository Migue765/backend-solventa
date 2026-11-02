from uuid import UUID, uuid4

import pytest
from bff_web.domain.auth import User
from bff_web.domain.client_information import (
    ClientInformationError,
    ClientInformationResult,
    ClientInformationState,
)
from flask import Flask
from support import USER, FakeIdentity, KeyPair, bearer, sign_token

PARTNER = User(
    UUID("5323bf51-1899-4a3e-9547-b8ccb9c2c84f"),
    "Banco Aliado",
    "operaciones@banco.co",
    "socio",
    partner_ids=("BANCO-001",),
)


class ClientInformationGatewayStub:
    def __init__(self) -> None:
        self.payload: dict[str, object] | None = None
        self.result: ClientInformationResult | None = None
        self.state_result: ClientInformationState | None = None
        self.state_calls: list[tuple[str, UUID, UUID]] = []
        self.error: ClientInformationError | None = None

    def consult(self, payload: dict[str, object], correlation_id: str) -> ClientInformationResult:
        if self.error is not None:
            raise self.error
        self.payload = payload
        response = {key: value for key, value in payload.items() if key != "datosSolicitados"}
        self.result = ClientInformationResult.model_validate(
            {
                **response,
                "estado": "completada",
                "consentimientoId": str(uuid4()),
                "consentimientoVersion": 1,
                "datos": {"indicadorRiesgo": "0.50"},
                "evidenciaFuentes": [
                    {
                        "fuente": "open_finance",
                        "version": "mock-1",
                        "consultadaEn": "2026-10-24T20:00:00Z",
                        "simulada": True,
                    }
                ],
                "consultadaEn": "2026-10-24T20:00:00Z",
            }
        )
        return self.result

    def state(
        self, socio_id: str, operation_id: UUID, actor_id: UUID, correlation_id: str
    ) -> ClientInformationState:
        self.state_calls.append((socio_id, operation_id, actor_id))
        if self.error is not None:
            raise self.error
        assert self.state_result is not None
        return self.state_result

    def recover(
        self, socio_id: str, operation_id: UUID, correlation_id: str
    ) -> ClientInformationResult:
        assert self.result is not None
        assert self.result.partner_id == socio_id
        assert self.result.operation_id == operation_id
        return self.result


def _token(keypair: KeyPair) -> str:
    return sign_token(
        keypair,
        user=PARTNER,
        scope="socio",
        socios=["BANCO-001"],
    )


def _state(operation: UUID) -> ClientInformationState:
    return ClientInformationState.model_validate(
        {
            "operationId": str(operation),
            "socioId": "BANCO-001",
            "clienteId": str(USER.user_id),
            "productoId": "automotive",
            "excepcionId": str(uuid4()),
            "estadoDecision": "pendiente",
            "aplicacion": {
                "estado": "esperando_decision",
                "intentos": 0,
                "codigo": None,
                "actualizadaEn": "2026-11-01T22:00:00Z",
                "acuseId": None,
            },
        }
    )


def test_requester_state_forwards_authenticated_actor_and_enforces_partner(
    app: Flask,
    keypair: KeyPair,
) -> None:
    gateway = ClientInformationGatewayStub()
    operation = uuid4()
    gateway.state_result = _state(operation)
    app.extensions["client_information_gateway"] = gateway
    path = f"/v1/socios/consultas-informacion-cliente/{operation}/estado"
    client = app.test_client()
    headers = bearer(_token(keypair))
    assert client.get(path, query_string={"socioId": "BANCO-001"}).status_code == 401
    assert client.get(path, query_string={"socioId": "OTRO"}, headers=headers).status_code == 403
    assert not gateway.state_calls
    response = client.get(path, query_string={"socioId": "BANCO-001"}, headers=headers)
    assert response.status_code == 200
    assert response.get_json() == gateway.state_result.model_dump(mode="json", by_alias=True)
    assert gateway.state_calls == [("BANCO-001", operation, PARTNER.user_id)]
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["API-Version"] == "1"


@pytest.mark.parametrize(
    ("code", "status", "public"),
    [
        ("consulta_no_encontrada", 404, "consulta_no_encontrada"),
        ("actor_no_autorizado", 403, "actor_no_autorizado"),
        ("consentimiento_no_vigente", 403, "consentimiento_invalido"),
        ("identidad_no_disponible", 503, "servicio_no_disponible"),
    ],
)
def test_requester_state_uses_standard_problem_contract(
    app: Flask,
    keypair: KeyPair,
    code: str,
    status: int,
    public: str,
) -> None:
    gateway = ClientInformationGatewayStub()
    gateway.error = ClientInformationError(code, status)
    app.extensions["client_information_gateway"] = gateway
    response = app.test_client().get(
        f"/v1/socios/consultas-informacion-cliente/{uuid4()}/estado?socioId=BANCO-001",
        headers=bearer(_token(keypair)),
    )
    assert response.status_code == status
    assert response.content_type == "application/problem+json"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.get_json()["code"] == public
    assert set(response.get_json()) == {
        "type",
        "title",
        "detail",
        "status",
        "code",
        "correlationId",
    }
    assert str(UUID(response.get_json()["correlationId"])) == response.headers["X-Correlation-ID"]


@pytest.mark.parametrize(
    ("code", "status", "public"),
    [
        ("fuente_no_disponible", 503, "consulta_bloqueada"),
        ("operacion_rechazada", 409, "operacion_rechazada"),
    ],
)
def test_blocked_post_preserves_operation_without_overriding_problem_fields(
    app: Flask,
    keypair: KeyPair,
    code: str,
    status: int,
    public: str,
) -> None:
    gateway = ClientInformationGatewayStub()
    operation = uuid4()
    state = _state(operation)
    gateway.error = ClientInformationError(code, status, state)
    app.extensions["client_information_gateway"] = gateway
    response = app.test_client().post(
        "/v1/socios/consultas-informacion-cliente",
        json={
            "operationId": str(operation),
            "socioId": "BANCO-001",
            "clienteId": str(USER.user_id),
            "productoId": "automotive",
            "finalidad": "cotizacion",
            "datosSolicitados": ["indicador_riesgo"],
        },
        headers={**bearer(_token(keypair)), "Idempotency-Key": str(operation)},
    )
    body = response.get_json()
    assert response.status_code == status and body["status"] == status
    assert response.content_type == "application/problem+json"
    assert response.headers["Cache-Control"] == "no-store"
    assert body["code"] == public
    assert body["operacion"] == state.model_dump(mode="json", by_alias=True)


def test_partner_consults_only_for_authorized_context(app: Flask, keypair: KeyPair) -> None:
    gateway = ClientInformationGatewayStub()
    app.extensions["client_information_gateway"] = gateway
    operation_id = uuid4()

    response = app.test_client().post(
        "/v1/socios/consultas-informacion-cliente",
        json={
            "operationId": str(operation_id),
            "socioId": "BANCO-001",
            "clienteId": str(USER.user_id),
            "productoId": "automotive",
            "finalidad": "cotizacion",
            "datosSolicitados": ["indicador_riesgo", "movimientos"],
        },
        headers={
            "Authorization": f"Bearer {_token(keypair)}",
            "Idempotency-Key": str(operation_id),
            "API-Version": "1",
        },
    )

    assert response.status_code == 201
    assert response.headers["Cache-Control"] == "no-store"
    assert response.get_json()["datos"] == {"indicadorRiesgo": "0.50"}
    assert gateway.payload is not None
    assert gateway.payload["actorId"] == str(PARTNER.user_id)
    assert gateway.payload["clienteId"] == str(USER.user_id)


def test_partner_resolves_client_by_document_before_consulting(
    app: Flask, keypair: KeyPair
) -> None:
    gateway = ClientInformationGatewayStub()
    app.extensions["client_information_gateway"] = gateway
    identity: FakeIdentity = app.extensions["identity_gateway"]
    operation_id = uuid4()

    response = app.test_client().post(
        "/v1/socios/consultas-informacion-cliente",
        json={
            "operationId": str(operation_id),
            "socioId": "BANCO-001",
            "tipoDocumento": "cc",
            "numeroDocumento": "1234567890",
            "productoId": "automotive",
            "finalidad": "cotizacion",
            "datosSolicitados": ["indicador_riesgo"],
        },
        headers={
            "Authorization": f"Bearer {_token(keypair)}",
            "Idempotency-Key": str(operation_id),
        },
    )

    assert response.status_code == 201
    assert gateway.payload is not None
    assert gateway.payload["clienteId"] == str(USER.user_id)
    assert identity.calls[-1][0] == "get_client_by_document"
    assert identity.calls[-1][1][:2] == ("cc", "1234567890")


def test_partner_recovers_authorized_information_by_operation(app: Flask, keypair: KeyPair) -> None:
    gateway = ClientInformationGatewayStub()
    app.extensions["client_information_gateway"] = gateway
    operation_id = uuid4()
    gateway.consult(
        {
            "operationId": str(operation_id),
            "socioId": "BANCO-001",
            "actorId": str(PARTNER.user_id),
            "clienteId": str(USER.user_id),
            "productoId": "mortgage-life",
            "finalidad": "cotizacion",
            "datosSolicitados": ["indicador_riesgo"],
        },
        "corr-base",
    )

    response = app.test_client().get(
        f"/v1/socios/consultas-informacion-cliente/{operation_id}",
        query_string={"socioId": "BANCO-001"},
        headers={**bearer(_token(keypair)), "API-Version": "1"},
    )

    assert response.status_code == 200
    assert response.get_json()["operationId"] == str(operation_id)


def test_client_identifier_modes_are_exclusive_and_missing_document_is_not_found(
    app: Flask, keypair: KeyPair
) -> None:
    identity: FakeIdentity = app.extensions["identity_gateway"]
    operation_id = uuid4()
    base = {
        "operationId": str(operation_id),
        "socioId": "BANCO-001",
        "productoId": "automotive",
        "finalidad": "cotizacion",
        "datosSolicitados": ["indicador_riesgo"],
    }
    headers = {
        "Authorization": f"Bearer {_token(keypair)}",
        "Idempotency-Key": str(operation_id),
    }

    mixed = app.test_client().post(
        "/v1/socios/consultas-informacion-cliente",
        json={
            **base,
            "clienteId": str(USER.user_id),
            "tipoDocumento": "cc",
            "numeroDocumento": "1234567890",
        },
        headers=headers,
    )
    identity.clients = ()
    missing = app.test_client().post(
        "/v1/socios/consultas-informacion-cliente",
        json={
            **base,
            "tipoDocumento": "cc",
            "numeroDocumento": "1099999999",
        },
        headers=headers,
    )

    assert mixed.status_code == 400
    assert missing.status_code == 404
    assert missing.get_json()["code"] == "cliente_no_encontrado"


def test_partner_cannot_consult_for_another_organization(app: Flask, keypair: KeyPair) -> None:
    operation_id = uuid4()

    response = app.test_client().post(
        "/v1/socios/consultas-informacion-cliente",
        json={
            "operationId": str(operation_id),
            "socioId": "BANCO-999",
            "clienteId": str(USER.user_id),
            "productoId": "automotive",
            "finalidad": "cotizacion",
            "datosSolicitados": ["indicador_riesgo"],
        },
        headers={
            "Authorization": f"Bearer {_token(keypair)}",
            "Idempotency-Key": str(operation_id),
        },
    )

    assert response.status_code == 403
    assert response.get_json()["code"] == "socio_no_autorizado"
