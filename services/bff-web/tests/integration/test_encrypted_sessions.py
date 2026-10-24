from dataclasses import replace
from typing import Any

import pytest
from bff_web.app import create_app
from bff_web.config import Config
from flask import Flask
from support import ISSUER, IdentityServer, KeyPair, sign_token, token_response


@pytest.fixture
def encrypted_bff(identity_server: IdentityServer, keypair: KeyPair) -> Flask:
    identity_server.respond("POST", "/oauth/token", 200, token_response(sign_token(keypair)))
    identity_server.respond(
        "GET",
        "/oauth/login-challenge",
        200,
        {"desafioId": "opaque", "clavePublica": {"kid": "key"}, "expiraEn": "2026-10-24T00:00:00Z"},
    )
    return create_app(
        Config(identity_url=identity_server.url, jwt_issuer=ISSUER, encrypted_login_enabled=True)
    )


def test_challenge_is_public_and_not_cached(
    encrypted_bff: Flask, identity_server: IdentityServer
) -> None:
    response = encrypted_bff.test_client().get("/v1/sesiones/desafio")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.get_json()["desafioId"] == "opaque"
    assert identity_server.requests[0]["path"] == "/oauth/login-challenge"


def test_envelope_forwarded_without_plaintext(
    encrypted_bff: Flask, identity_server: IdentityServer
) -> None:
    compact = "protected.key.iv.ciphertext.tag"
    response = encrypted_bff.test_client().post(
        "/v1/sesiones", json={"correo": "ana@solventa.co", "credencialCifrada": compact}
    )
    assert response.status_code == 200
    request = identity_server.requests[0]
    assert request["body"] == {
        "grant_type": "encrypted_password",
        "client_id": "web",
        "username": "ana@solventa.co",
        "credencialCifrada": compact,
    }
    assert "password" not in request["body"]
    assert "contrasena" not in request["body"]


@pytest.mark.parametrize(
    "payload",
    [
        {"correo": "ana@solventa.co", "contrasena": "Solventa123!"},
        {"correo": "ana@solventa.co", "credencialCifrada": "x", "contrasena": "Solventa123!"},
        {"correo": "ana@solventa.co"},
        {"correo": "invalid", "credencialCifrada": "x"},
        {"correo": "ana@solventa.co", "credencialCifrada": ""},
        {"correo": "ana@solventa.co", "credencialCifrada": "x" * 8193},
        {"correo": "ana@solventa.co", "credencialCifrada": 123},
        {"correo": "ana@solventa.co", "credencialCifrada": "x", "extra": True},
        {"padding": "x" * 17000},
    ],
)
def test_strict_encrypted_fields(
    encrypted_bff: Flask, identity_server: IdentityServer, payload: dict[str, Any]
) -> None:
    response = encrypted_bff.test_client().post("/v1/sesiones", json=payload)
    assert response.status_code == 400
    assert response.get_json()["code"] == "solicitud_invalida"
    assert not identity_server.requests


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (400, "solicitud_invalida"),
        (401, "credenciales_invalidas"),
        (429, "demasiados_intentos"),
        (503, "servicio_no_disponible"),
    ],
)
def test_rejection_without_fallback(
    encrypted_bff: Flask, identity_server: IdentityServer, status: int, code: str
) -> None:
    identity_server.respond("POST", "/oauth/token", status, {"code": code})
    response = encrypted_bff.test_client().post(
        "/v1/sesiones", json={"correo": "ana@solventa.co", "credencialCifrada": "opaque"}
    )
    assert response.status_code == status
    assert len(identity_server.requests) == 1
    if status in {429, 503}:
        assert int(response.headers["Retry-After"]) > 0


def test_disabled_challenge_and_envelope_never_fallback(
    encrypted_bff: Flask, identity_server: IdentityServer
) -> None:
    encrypted_bff.extensions["bff_config"] = replace(
        encrypted_bff.extensions["bff_config"], encrypted_login_enabled=False
    )
    client = encrypted_bff.test_client()
    challenge = client.get("/v1/sesiones/desafio")
    assert challenge.status_code == 503
    assert challenge.headers["Cache-Control"] == "no-store"
    assert (
        client.post(
            "/v1/sesiones", json={"correo": "ana@solventa.co", "credencialCifrada": "opaque"}
        ).status_code
        == 400
    )
    assert not identity_server.requests


def test_identity_challenge_unavailable(
    encrypted_bff: Flask, identity_server: IdentityServer
) -> None:
    identity_server.respond(
        "GET", "/oauth/login-challenge", 503, {"code": "servicio_no_disponible"}
    )
    response = encrypted_bff.test_client().get("/v1/sesiones/desafio")
    assert response.status_code == 503
    assert response.headers["Cache-Control"] == "no-store"
