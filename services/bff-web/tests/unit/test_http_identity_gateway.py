import socket
from typing import Any
from uuid import uuid4

import pytest
from bff_web.domain.errors import IdentityRejectedError, IdentityUnavailableError
from bff_web.infrastructure.http_identity_gateway import HttpIdentityGateway
from bff_web.infrastructure.jwt_verifier import http_jwks_fetcher
from support import ACCESS_TTL, REFRESH_TTL, USER, IdentityServer, KeyPair, token_response

CID = "7a7f3a9e-1c1e-4f4a-9a53-0b6b2c5f9d11"


@pytest.mark.parametrize("active", [True, False])
def test_session_state_uses_internal_auth_and_preserves_revocation(
    identity_server: IdentityServer, active: bool
) -> None:
    session_id = uuid4()
    identity_server.respond(
        "GET",
        f"/v1/sesiones/{session_id}",
        200,
        {"sesionId": str(session_id), "activa": active},
    )
    gateway = HttpIdentityGateway(identity_server.url, internal_api_token="test-internal")
    assert gateway.session_active(session_id, CID) is active
    assert identity_server.requests[0]["headers"]["Authorization"] == "Bearer test-internal"


def test_identity_gateway_preserves_explicit_internal_permissions(
    identity_server: IdentityServer,
) -> None:
    payload = token_response()
    payload["scope"] = "asesor"
    payload["usuario"].update(
        rol="asesor", socios=[], permisos=["siniestros:revisar", "reportes:operativos"]
    )
    identity_server.respond("POST", "/oauth/token", 200, payload)
    tokens = HttpIdentityGateway(identity_server.url).login(USER.email, "password", CID)
    assert tokens.user.role == "asesor"
    assert tokens.user.partner_ids == ()
    assert tokens.user.permissions == ("siniestros:revisar", "reportes:operativos")


@pytest.mark.parametrize(
    "payload", [{"sesionId": "wrong", "activa": True}, {"activa": True}, {"activa": "true"}]
)
def test_session_state_rejects_mismatched_or_malformed_response(
    identity_server: IdentityServer, payload: dict[str, object]
) -> None:
    session_id = uuid4()
    identity_server.respond("GET", f"/v1/sesiones/{session_id}", 200, payload)
    with pytest.raises(IdentityUnavailableError):
        HttpIdentityGateway(identity_server.url, internal_api_token="test").session_active(
            session_id, CID
        )


@pytest.mark.parametrize("operation", ["request", "confirm"])
def test_recovery_rejects_unexpected_upstream_success_status(
    identity_server: IdentityServer, operation: str
) -> None:
    path = "/v1/recuperacion-contrasena" + ("/confirmar" if operation == "confirm" else "")
    identity_server.respond(
        "POST",
        path,
        200,
        {"mensaje": "Si existe una cuenta con ese correo, recibiras un enlace de recuperacion."},
    )
    gateway = HttpIdentityGateway(identity_server.url)
    with pytest.raises(IdentityUnavailableError):
        if operation == "request":
            gateway.request_password_recovery("test@example.com", CID)
        else:
            gateway.confirm_password_recovery("a" * 64, "test-jwe", CID)


@pytest.mark.parametrize(
    "payload",
    [
        {"mensaje": "unknown"},
        {"mensaje": None},
        {
            "mensaje": "Si existe una cuenta con ese correo, recibiras un enlace de recuperacion.",
            "token": "must-not-be-exposed",
        },
    ],
)
def test_recovery_rejects_unknown_or_extra_fields_from_identity(
    identity_server: IdentityServer, payload: dict[str, object]
) -> None:
    identity_server.respond("POST", "/v1/recuperacion-contrasena", 202, payload)
    with pytest.raises(IdentityUnavailableError):
        HttpIdentityGateway(identity_server.url).request_password_recovery("test@example.com", CID)


def registration_request() -> dict[str, object]:
    return {
        "nombre": "Ana",
        "correo": "ana@solventa.co",
        "credencialCifrada": "test-jwe",
        "tipoDocumento": "passport",
        "numeroDocumento": "test123",
        "telefono": "+573001234567",
        "fechaNacimiento": "2010-01-01",
        "consentimiento": {"autorizado": False, "versionTerminos": "1.0"},
    }


@pytest.mark.parametrize("missing", [None, "numeroDocumento", "telefono"])
def test_encrypted_registration_preserves_fields_and_rejects_incomplete_confirmation(
    identity_server: IdentityServer, missing: str | None
) -> None:
    data = {
        "id": str(USER.user_id),
        "nombre": "Ana",
        "correo": "ana@solventa.co",
        "rol": "cliente",
        "tipoDocumento": "passport",
        "numeroDocumento": "TEST123",
        "telefono": "+573001234567",
        "fechaNacimiento": "2010-01-01",
    }
    if missing is not None:
        data.pop(missing)
    identity_server.respond("POST", "/v1/usuarios", 201, {"usuario": data})
    gateway = HttpIdentityGateway(identity_server.url)
    if missing is not None:
        with pytest.raises(IdentityUnavailableError):
            gateway.encrypted_register(registration_request(), CID)
    else:
        user = gateway.encrypted_register(registration_request(), CID)
        assert user.identification == "TEST123" and user.role == "cliente"
    assert identity_server.requests[0]["body"] == registration_request()


def test_registration_document_conflict_is_not_hidden_as_an_outage(
    identity_server: IdentityServer,
) -> None:
    identity_server.respond("POST", "/v1/usuarios", 409, {"code": "documento_ya_registrado"})
    with pytest.raises(IdentityRejectedError) as raised:
        HttpIdentityGateway(identity_server.url).encrypted_register(registration_request(), CID)
    assert raised.value.status == 409 and raised.value.code == "documento_ya_registrado"


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


def test_get_client_by_document_encodes_query_and_validates_identity_response(
    identity_server: IdentityServer,
) -> None:
    path = "/v1/clientes?tipoDocumento=passport&numeroDocumento=AB12345"
    identity_server.respond(
        "GET",
        path,
        200,
        {
            "clienteId": str(USER.user_id),
            "nombre": USER.name,
            "correo": USER.email,
            "tipoDocumento": "passport",
            "numeroDocumento": "AB12345",
        },
    )

    client = HttpIdentityGateway(
        identity_server.url, internal_api_token="internal"
    ).get_client_by_document("passport", "AB12345", CID)

    assert client is not None
    assert client.user_id == USER.user_id
    assert (client.document_type, client.identification) == ("passport", "AB12345")
    assert identity_server.requests[0]["path"] == path


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
