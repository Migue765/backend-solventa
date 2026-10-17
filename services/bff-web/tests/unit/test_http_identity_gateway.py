import socket
from typing import Any

import pytest
from bff_web.domain.errors import IdentityRejectedError, IdentityUnavailableError
from bff_web.infrastructure.http_identity_gateway import HttpIdentityGateway
from bff_web.infrastructure.jwt_verifier import http_jwks_fetcher
from support import ACCESS_TTL, REFRESH_TTL, USER, IdentityServer, KeyPair, token_response

CID = "7a7f3a9e-1c1e-4f4a-9a53-0b6b2c5f9d11"


def closed_port_url() -> str:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{probe.getsockname()[1]}"


def test_login_sends_the_password_grant_for_the_web_client(identity_server: IdentityServer) -> None:
    identity_server.respond("POST", "/oauth/token", 200, token_response())

    tokens = HttpIdentityGateway(identity_server.url).login("ana@solventa.co", "Solventa123!", CID)

    request = identity_server.requests[0]
    assert request["body"] == {
        "grant_type": "password",
        "client_id": "web",
        "username": "ana@solventa.co",
        "password": "Solventa123!",
    }
    assert request["headers"]["X-Correlation-Id"] == CID
    assert tokens.access_token == "access-jwt"
    assert tokens.refresh_token == "refresh-opaque"
    assert (tokens.expires_in, tokens.refresh_expires_in) == (ACCESS_TTL, REFRESH_TTL)
    assert tokens.user == USER


def test_refresh_and_revoke_requests(identity_server: IdentityServer) -> None:
    identity_server.respond("POST", "/oauth/token", 200, token_response())
    identity_server.respond("POST", "/oauth/revoke", 204)
    gateway = HttpIdentityGateway(identity_server.url, client_id="movil")

    gateway.refresh("refresh-1", CID)
    gateway.revoke("refresh-1", CID)

    assert identity_server.requests[0]["body"] == {
        "grant_type": "refresh_token",
        "client_id": "movil",
        "refresh_token": "refresh-1",
    }
    assert identity_server.requests[1]["path"] == "/oauth/revoke"
    assert identity_server.requests[1]["body"] == {"refresh_token": "refresh-1"}


def test_register_returns_the_created_user(identity_server: IdentityServer) -> None:
    identity_server.respond(
        "POST",
        "/v1/usuarios",
        201,
        {
            "usuario": {
                "id": str(USER.user_id),
                "nombre": "Ana",
                "correo": "a@s.co",
                "rol": "cliente",
            }
        },
    )

    user = HttpIdentityGateway(identity_server.url).register("Ana", "a@s.co", "Solventa123!", CID)

    assert (user.user_id, user.name, user.email, user.role) == (
        USER.user_id,
        "Ana",
        "a@s.co",
        "cliente",
    )
    assert identity_server.requests[0]["body"] == {
        "nombre": "Ana",
        "correo": "a@s.co",
        "contrasena": "Solventa123!",
    }


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (400, "solicitud_invalida"),
        (401, "credenciales_invalidas"),
        (401, "refresh_invalido"),
        (409, "correo_ya_registrado"),
    ],
)
def test_known_business_rejections_are_preserved(
    identity_server: IdentityServer, status: int, code: str
) -> None:
    identity_server.respond("POST", "/oauth/token", status, {"code": code, "status": status})

    with pytest.raises(IdentityRejectedError) as error:
        HttpIdentityGateway(identity_server.url).login("a@s.co", "Solventa123!", CID)

    assert (error.value.status, error.value.code, error.value.retry_after) == (status, code, None)


def test_rate_limit_keeps_retry_after_or_defaults_it(identity_server: IdentityServer) -> None:
    body = {"code": "demasiados_intentos", "status": 429}
    identity_server.respond("POST", "/oauth/token", 429, body, {"Retry-After": "30"})
    identity_server.respond("POST", "/v1/usuarios", 429, body)
    gateway = HttpIdentityGateway(identity_server.url)

    with pytest.raises(IdentityRejectedError) as with_header:
        gateway.login("a@s.co", "Solventa123!", CID)
    with pytest.raises(IdentityRejectedError) as without_header:
        gateway.register("Ana", "a@s.co", "Solventa123!", CID)

    assert with_header.value.retry_after == 30
    assert without_header.value.retry_after == 60


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (500, {"code": "error_interno"}),
        (503, {"code": "servicio_no_disponible"}),
        (503, {"code": "credenciales_invalidas"}),
        (401, {"code": "codigo_desconocido"}),
        (404, {"detail": "sin code"}),
        (418, None),
    ],
)
def test_server_errors_and_unknown_codes_are_unavailable(
    identity_server: IdentityServer, status: int, body: Any
) -> None:
    identity_server.respond("POST", "/oauth/token", status, body)

    with pytest.raises(IdentityUnavailableError):
        HttpIdentityGateway(identity_server.url).login("a@s.co", "Solventa123!", CID)


@pytest.mark.parametrize(
    "body",
    [
        b"no es json",
        b"[]",
        b"{}",
        b'{"access_token": "x"}',
        b'{"access_token":"x","refresh_token":"r","expires_in":"3600","refresh_expires_in":1,'
        b'"usuario":{}}',
        b'{"access_token":"x","refresh_token":"r","expires_in":0,"refresh_expires_in":1,'
        b'"usuario":{}}',
    ],
)
def test_malformed_success_responses_are_unavailable(
    identity_server: IdentityServer, body: bytes
) -> None:
    identity_server.respond_raw("POST", "/oauth/token", 200, body)

    with pytest.raises(IdentityUnavailableError):
        HttpIdentityGateway(identity_server.url).login("a@s.co", "Solventa123!", CID)


def test_unreachable_identity_is_unavailable() -> None:
    with pytest.raises(IdentityUnavailableError):
        HttpIdentityGateway(closed_port_url(), timeout_seconds=1).login("a@s.co", "x" * 8, CID)


def test_slow_identity_times_out_as_unavailable(identity_server: IdentityServer) -> None:
    identity_server.respond("POST", "/oauth/token", 200, token_response(), delay=1)

    with pytest.raises(IdentityUnavailableError):
        HttpIdentityGateway(identity_server.url, timeout_seconds=0.2).login(
            "a@s.co", "Solventa123!", CID
        )


def test_jwks_fetcher_reads_the_document(identity_server: IdentityServer, keypair: KeyPair) -> None:
    identity_server.respond("GET", "/.well-known/jwks.json", 200, {"keys": [keypair.jwk()]})

    document = http_jwks_fetcher(f"{identity_server.url}/.well-known/jwks.json")()

    assert document["keys"][0]["kid"] == keypair.kid


@pytest.mark.parametrize("status", [404, 500])
def test_jwks_fetcher_failures_are_unavailable(
    identity_server: IdentityServer, status: int
) -> None:
    identity_server.respond("GET", "/.well-known/jwks.json", status, {"keys": []})

    with pytest.raises(IdentityUnavailableError):
        http_jwks_fetcher(f"{identity_server.url}/.well-known/jwks.json")()


def test_jwks_fetcher_rejects_non_json_and_unreachable_hosts(
    identity_server: IdentityServer,
) -> None:
    identity_server.respond_raw("GET", "/.well-known/jwks.json", 200, b"<html>")

    with pytest.raises(IdentityUnavailableError):
        http_jwks_fetcher(f"{identity_server.url}/.well-known/jwks.json")()
    with pytest.raises(IdentityUnavailableError):
        http_jwks_fetcher(f"{closed_port_url()}/.well-known/jwks.json", 1)()
