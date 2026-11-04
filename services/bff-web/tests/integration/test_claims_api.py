from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest
from bff_web.domain.claims import (
    ClaimNotFoundError,
    ClaimServiceUnavailableError,
    ClaimStatus,
)
from flask import Flask
from support import USER, KeyPair, sign_token

CLAIM_ID = UUID("6f1d2c1a-8a52-4b1e-9d0f-3c2b1a0e9f77")


@pytest.fixture
def token(keypair: KeyPair) -> str:
    return sign_token(keypair, scope="cliente")


def claim_status() -> ClaimStatus:
    return ClaimStatus.model_validate(
        {
            "id": str(CLAIM_ID),
            "polizaId": str(uuid4()),
            "tipo": "asistido",
            "estado": "en_evaluacion",
            "descripcion": "Estamos evaluando la informacion del caso.",
            "ocurridoEn": "2026-11-01T09:30:00-05:00",
            "reportadoEn": "2026-11-01T15:00:00Z",
            "actualizadoEn": "2026-11-01T16:00:00Z",
            "historial": [
                {
                    "estado": "reportado",
                    "descripcion": "Recibimos el aviso de tu siniestro.",
                    "ocurridoEn": "2026-11-01T15:00:00Z",
                },
                {
                    "estado": "en_evaluacion",
                    "descripcion": "Estamos evaluando la informacion del caso.",
                    "ocurridoEn": "2026-11-01T16:00:00Z",
                },
            ],
        }
    )


@dataclass
class StubClaimGateway:
    failure: Exception | None = None
    calls: list[tuple[UUID, UUID]] = field(default_factory=list)

    def get_status(self, claim_id: UUID, client_id: UUID, correlation_id: str) -> ClaimStatus:
        self.calls.append((claim_id, client_id))
        if self.failure is not None:
            raise self.failure
        return claim_status()


@pytest.fixture
def claims(app: Flask) -> StubClaimGateway:
    gateway = StubClaimGateway()
    app.extensions["claim_gateway"] = gateway
    return gateway


def path(claim_id: object = CLAIM_ID) -> str:
    return f"/v1/clientes/me/siniestros/{claim_id}"


def test_consulta_estado_e_historial_del_cliente_de_la_sesion(
    app: Flask, claims: StubClaimGateway, token: str
) -> None:
    response = app.test_client().get(path(), headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    body = response.get_json()
    assert body["estado"] == "en_evaluacion"
    assert body["tipo"] == "asistido"
    assert body["ocurridoEn"] == "2026-11-01T14:30:00Z"
    assert body["actualizadoEn"] == "2026-11-01T16:00:00Z"
    assert [entry["estado"] for entry in body["historial"]] == ["reportado", "en_evaluacion"]
    assert response.headers["Cache-Control"] == "no-store"
    assert claims.calls == [(CLAIM_ID, USER.user_id)]


@pytest.mark.parametrize("credential", [None, "Bearer invalido"])
def test_requiere_sesion_valida(
    app: Flask, claims: StubClaimGateway, credential: str | None
) -> None:
    headers = {"Authorization": credential} if credential else {}

    response = app.test_client().get(path(), headers=headers)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert claims.calls == []


def test_token_de_otro_canal_no_consulta(
    app: Flask, claims: StubClaimGateway, keypair: KeyPair
) -> None:
    mobile = sign_token(keypair, client_id="movil")

    response = app.test_client().get(path(), headers={"Authorization": f"Bearer {mobile}"})

    assert response.status_code == 401
    assert claims.calls == []


@pytest.mark.parametrize("role,scope", [("cliente", "cotizaciones"), ("socio", "cliente")])
def test_requiere_rol_y_scope_cliente(
    app: Flask, claims: StubClaimGateway, keypair: KeyPair, role: str, scope: str
) -> None:
    credential = sign_token(keypair, rol=role, scope=scope)
    response = app.test_client().get(path(), headers={"Authorization": "Bearer " + credential})
    assert response.status_code == 403
    assert claims.calls == []
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("target", [path("no-es-uuid"), path() + "?clienteId=" + str(uuid4())])
def test_el_cliente_no_se_acepta_desde_el_navegador(
    app: Flask, claims: StubClaimGateway, token: str, target: str
) -> None:
    response = app.test_client().get(target, headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 400
    assert response.get_json()["code"] == "solicitud_invalida"
    assert claims.calls == []


def test_inexistente_o_ajeno_no_revela_datos(
    app: Flask, claims: StubClaimGateway, token: str
) -> None:
    claims.failure = ClaimNotFoundError()

    response = app.test_client().get(path(), headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 404
    body = response.get_json()
    assert body["code"] == "siniestro_no_encontrado"
    assert str(CLAIM_ID) not in response.get_data(as_text=True)
    assert "historial" not in body and "estado" not in body


def test_fallo_no_es_un_estado_ni_un_historial_vacio(
    app: Flask, claims: StubClaimGateway, token: str
) -> None:
    claims.failure = ClaimServiceUnavailableError()

    response = app.test_client().get(path(), headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 503
    assert response.content_type == "application/problem+json"
    assert response.headers["Retry-After"] == "5"
    assert response.get_json()["code"] == "servicio_no_disponible"
    assert "historial" not in response.get_json()
