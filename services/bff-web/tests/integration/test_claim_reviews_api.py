from unittest.mock import Mock
from uuid import uuid4

import pytest
from bff_web.domain.claim_review import ClaimReviewError
from bff_web.infrastructure.http_claim_review_gateway import HttpClaimReviewGateway
from flask import Flask
from support import KeyPair, sign_token

QUEUE = "/v1/administracion/colas-revision/revision/asignaciones"


@pytest.mark.parametrize(
    "role,scope,partners",
    [("cliente", "cliente", []), ("socio", "socio", ["BANCO"]), ("asesor", "asesor", ["BANCO"])],
)
def test_solo_administrador_puede_acceder(
    app: Flask, keypair: KeyPair, role: str, scope: str, partners: list[str]
) -> None:
    gateway = Mock(spec=HttpClaimReviewGateway)
    app.extensions["claim_review_gateway"] = gateway
    token = sign_token(keypair, rol=role, scope=scope, socios=partners, sid=str(uuid4()))
    response = app.test_client().get(QUEUE, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403
    assert response.get_json()["code"] == "alcance_insuficiente"
    assert gateway.mock_calls == []


@pytest.mark.parametrize(
    "scope,sid,partners,client_id",
    [
        ("administrador", None, [], "web"),
        ("administrador", "sesion-invalida", [], "web"),
        ("asesor", str(uuid4()), [], "web"),
        ("administrador", str(uuid4()), ["BANCO"], "web"),
        ("administrador", str(uuid4()), [], "movil"),
    ],
)
def test_administrador_requiere_canal_scope_sesion_y_ausencia_de_socios(
    app: Flask,
    keypair: KeyPair,
    scope: str,
    sid: str | None,
    partners: list[str],
    client_id: str,
) -> None:
    gateway = Mock(spec=HttpClaimReviewGateway)
    app.extensions["claim_review_gateway"] = gateway
    token = sign_token(
        keypair, rol="administrador", scope=scope, sid=sid, socios=partners, client_id=client_id
    )
    response = app.test_client().get(QUEUE, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert gateway.mock_calls == []


@pytest.mark.parametrize(
    "body",
    [
        {"resultado": "aprobado", "motivo": "corto"},
        {"resultado": "aprobado", "motivo": " " * 20},
        {"resultado": "otro", "motivo": "Motivo suficientemente largo."},
        {
            "resultado": "aprobado",
            "motivo": "Motivo suficientemente largo.",
            "peritoId": str(uuid4()),
        },
        {
            "resultado": "aprobado",
            "motivo": "Motivo suficientemente largo.",
            "revisorId": str(uuid4()),
        },
    ],
)
def test_revision_rechaza_datos_y_actores_inyectados(
    app: Flask, keypair: KeyPair, body: dict[str, object]
) -> None:
    gateway = Mock(spec=HttpClaimReviewGateway)
    app.extensions["claim_review_gateway"] = gateway
    token = sign_token(keypair, rol="administrador", scope="administrador", sid=str(uuid4()))
    response = app.test_client().post(
        f"/v1/administracion/siniestros/{uuid4()}/decisiones/revision",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid4())},
        json=body,
    )
    assert response.status_code == 400
    assert gateway.mock_calls == []


def test_dependencia_caida_no_devuelve_cola_vacia(app: Flask, keypair: KeyPair) -> None:
    gateway = Mock(spec=HttpClaimReviewGateway)
    gateway.assignments.side_effect = ClaimReviewError()
    app.extensions["claim_review_gateway"] = gateway
    token = sign_token(keypair, rol="administrador", scope="administrador", sid=str(uuid4()))
    response = app.test_client().get(QUEUE, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 503
    assert response.get_json()["code"] == "servicio_no_disponible"
    assert "asignaciones" not in response.get_json()
    assert response.headers["Retry-After"] == "5"
    assert response.headers["Cache-Control"] == "no-store"
