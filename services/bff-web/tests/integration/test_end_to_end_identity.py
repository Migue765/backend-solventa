"""Flujo completo con HTTP real: el BFF habla con un servidor que simula a identidad."""

from collections.abc import Iterator
from typing import Any

import pytest
from bff_web.app import create_app
from bff_web.config import Config
from flask import Flask
from support import (
    ISSUER,
    USER,
    IdentityServer,
    KeyPair,
    bearer,
    sign_token,
    token_response,
)


@pytest.fixture
def real_app(identity_server: IdentityServer, keypair: KeyPair) -> Iterator[Flask]:
    identity_server.respond("GET", "/.well-known/jwks.json", 200, {"keys": [keypair.jwk()]})
    identity_server.respond("POST", "/oauth/token", 200, token_response(sign_token(keypair)))
    identity_server.respond("POST", "/oauth/revoke", 204)
    yield create_app(Config(identity_url=identity_server.url, jwt_issuer=ISSUER))


def test_login_then_use_the_issued_jwt(real_app: Flask, identity_server: IdentityServer) -> None:
    client = real_app.test_client()

    login = client.post(
        "/v1/sesiones", json={"correo": "ana@solventa.co", "contrasena": "Solventa123!"}
    )
    assert login.status_code == 200
    data: dict[str, Any] = login.get_json()

    current = client.get("/v1/sesiones/actual", headers=bearer(data["accessToken"]))
    assert current.status_code == 200
    assert current.get_json()["usuario"]["id"] == str(USER.user_id)

    logout = client.delete(
        "/v1/sesiones/actual",
        json={"refreshToken": data["refreshToken"]},
        headers=bearer(data["accessToken"]),
    )
    assert logout.status_code == 204
    paths = [request["path"] for request in identity_server.requests]
    assert paths == ["/oauth/token", "/.well-known/jwks.json", "/oauth/revoke"]


def test_identity_down_means_503_for_login_and_for_protected_routes(
    real_app: Flask, identity_server: IdentityServer, keypair: KeyPair
) -> None:
    identity_server.close()
    client = real_app.test_client()

    login = client.post(
        "/v1/sesiones", json={"correo": "ana@solventa.co", "contrasena": "Solventa123!"}
    )
    protected = client.get("/v1/sesiones/actual", headers=bearer(sign_token(keypair)))

    assert login.status_code == 503
    assert protected.status_code == 503
