from uuid import UUID, uuid4

from bff_web.domain.auth import User
from bff_web.domain.client_information import ClientInformationResult
from flask import Flask
from support import USER, KeyPair, sign_token

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

    def consult(self, payload: dict[str, object], correlation_id: str) -> ClientInformationResult:
        self.payload = payload
        response = {key: value for key, value in payload.items() if key != "datosSolicitados"}
        return ClientInformationResult.model_validate(
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


def _token(keypair: KeyPair) -> str:
    return sign_token(
        keypair,
        user=PARTNER,
        scope="socio",
        socios=["BANCO-001"],
    )


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
