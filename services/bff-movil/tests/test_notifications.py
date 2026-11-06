import json
from urllib.request import Request

import pytest
from bff_movil.api import notifications
from bff_movil.app import create_app
from bff_movil.config import Config
from bff_movil.infrastructure.jwt_verifier import JwksKeyStore, JwtVerifier
from flask import Flask
from support import AUDIENCE, ISSUER, FakeIdentity, FakeJwks, KeyPair, sign_token


class FakeResponse:
    status = 200

    def __init__(self, body: dict[str, object]) -> None:
        self._body = json.dumps(body).encode()

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self, _: int) -> bytes:
        return self._body


def test_notifications_requires_client_session(app: Flask, keypair: KeyPair) -> None:
    client = app.test_client()
    without_session = client.get("/movil/v1/notificaciones")
    wrong_scope = client.get(
        "/movil/v1/notificaciones",
        headers={"Authorization": f"Bearer {sign_token(keypair)}"},
    )

    assert without_session.status_code == 401
    assert wrong_scope.status_code == 403


def test_notifications_reports_missing_upstream_configuration(
    jwks: FakeJwks, identity: FakeIdentity, keypair: KeyPair
) -> None:
    app = create_app(
        Config(identity_url="http://identidad.test", jwt_issuer=ISSUER),
        identity_gateway=identity,
        token_verifier=JwtVerifier(JwksKeyStore(jwks.fetch), ISSUER, AUDIENCE, "movil"),
    )
    response = app.test_client().get(
        "/movil/v1/notificaciones",
        headers={"Authorization": f"Bearer {sign_token(keypair, scope='cliente')}"},
    )

    assert response.status_code == 503
    assert response.get_json()["code"] == "servicio_no_disponible"
    assert response.headers["Cache-Control"] == "no-store"


def test_notifications_forwards_authenticated_client_identity(
    jwks: FakeJwks,
    identity: FakeIdentity,
    keypair: KeyPair,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    forwarded: list[Request] = []

    def open_upstream(request: Request, timeout: float) -> FakeResponse:
        forwarded.append(request)
        assert timeout == 5
        return FakeResponse({"notificaciones": []})

    monkeypatch.setattr(notifications, "urlopen", open_upstream)
    app = create_app(
        Config(
            identity_url="http://identidad.test",
            jwt_issuer=ISSUER,
            notifications_service_url="http://notificaciones.test",
            internal_api_token="interno",
        ),
        identity_gateway=identity,
        token_verifier=JwtVerifier(JwksKeyStore(jwks.fetch), ISSUER, AUDIENCE, "movil"),
    )

    response = app.test_client().get(
        "/movil/v1/notificaciones",
        headers={"Authorization": f"Bearer {sign_token(keypair, scope='cliente')}"},
    )

    assert response.status_code == 200
    assert response.get_json() == {"notificaciones": []}
    assert len(forwarded) == 1
    request = forwarded[0]
    assert request.full_url == "http://notificaciones.test/v1/notificaciones"
    assert request.get_header("Authorization") == "Bearer interno"
    assert request.get_header("X-cliente-id") is not None
