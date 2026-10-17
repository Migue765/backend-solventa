from typing import Any

import pytest
from bff_web.domain.errors import IdentityUnavailableError
from flask import Flask
from support import (
    ACCESS_TTL,
    NOW,
    USER,
    FakeIdentity,
    FakeJwks,
    KeyPair,
    bearer,
    forge_hs256_token,
    forge_unsigned_token,
    rejected,
    sign_token,
)
from werkzeug.test import TestResponse

CREDENTIALS = {"correo": "ana@solventa.co", "contrasena": "Solventa123!"}
CID = "7a7f3a9e-1c1e-4f4a-9a53-0b6b2c5f9d11"


def body(response: TestResponse) -> dict[str, Any]:
    data = response.get_json()
    assert isinstance(data, dict)
    return data


def test_login_returns_tokens_without_cookies(app: Flask, identity: FakeIdentity) -> None:
    response = app.test_client().post(
        "/v1/sesiones", json=CREDENTIALS, headers={"X-Correlation-ID": CID}
    )

    assert response.status_code == 200
    data = body(response)
    assert data["tokenType"] == "Bearer"
    assert data["accessToken"] == "access-jwt"
    assert data["refreshToken"] == "refresh-opaque"
    assert data["usuario"] == {
        "id": str(USER.user_id),
        "nombre": "Ana Cliente",
        "correo": "ana@solventa.co",
        "rol": "cliente",
    }
    assert data["expiraEn"] == "2026-10-14T13:00:00Z"
    assert data["refreshExpiraEn"] == "2026-11-13T12:00:00Z"
    assert response.headers["Cache-Control"] == "no-store"
    assert "Set-Cookie" not in response.headers
    assert response.headers["X-Correlation-ID"] == CID
    assert identity.calls == [("login", ("ana@solventa.co", "Solventa123!", CID))]


def test_expiration_comes_from_the_value_identity_returns(
    app: Flask, identity: FakeIdentity
) -> None:
    from bff_web.domain.auth import TokenSet

    identity.login = lambda *_: TokenSet("a", "r", 120, 600, USER)  # type: ignore[method-assign]

    data = body(app.test_client().post("/v1/sesiones", json=CREDENTIALS))

    assert data["expiraEn"] == "2026-10-14T12:02:00Z"
    assert data["refreshExpiraEn"] == "2026-10-14T12:10:00Z"
    assert ACCESS_TTL == 3600


@pytest.mark.parametrize(
    "payload",
    [
        {"correo": "no-es-correo", "contrasena": "Solventa123!"},
        {"correo": "ana@solventa.co", "contrasena": "corta"},
        {"correo": "ana@solventa.co"},
        {**CREDENTIALS, "rol": "admin"},
        {},
    ],
)
def test_invalid_login_payload_never_reaches_identity(
    app: Flask, identity: FakeIdentity, payload: dict[str, str]
) -> None:
    response = app.test_client().post("/v1/sesiones", json=payload)

    assert response.status_code == 400
    assert response.content_type == "application/problem+json"
    assert body(response)["code"] == "solicitud_invalida"
    assert body(response)["correlationId"]
    assert identity.calls == []


def test_non_json_login_is_a_problem_response(app: Flask) -> None:
    response = app.test_client().post(
        "/v1/sesiones", data="correo=a", content_type="application/x-www-form-urlencoded"
    )

    assert response.status_code == 400
    assert response.content_type == "application/problem+json"


def test_invalid_credentials_are_translated(app: Flask, identity: FakeIdentity) -> None:
    identity.error = rejected(401, "credenciales_invalidas")

    response = app.test_client().post("/v1/sesiones", json=CREDENTIALS)

    assert response.status_code == 401
    assert body(response)["code"] == "credenciales_invalidas"
    assert "Retry-After" not in response.headers


def test_rate_limit_is_translated_with_retry_after(app: Flask, identity: FakeIdentity) -> None:
    identity.error = rejected(429, "demasiados_intentos", 45)

    response = app.test_client().post("/v1/sesiones", json=CREDENTIALS)

    assert response.status_code == 429
    assert body(response)["code"] == "demasiados_intentos"
    assert response.headers["Retry-After"] == "45"


def test_identity_outage_is_a_503(app: Flask, identity: FakeIdentity) -> None:
    identity.error = IdentityUnavailableError()

    response = app.test_client().post("/v1/sesiones", json=CREDENTIALS)

    assert response.status_code == 503
    assert body(response)["code"] == "servicio_no_disponible"
    assert "Retry-After" in response.headers


def test_renew_returns_a_new_token_set(app: Flask, identity: FakeIdentity) -> None:
    response = app.test_client().post(
        "/v1/sesiones/renovar",
        json={"refreshToken": "refresh-opaque"},
        headers={"X-Correlation-ID": CID},
    )

    assert response.status_code == 200
    assert body(response)["accessToken"] == "access-jwt"
    assert body(response)["expiraEn"] == "2026-10-14T13:00:00Z"
    assert response.headers["Cache-Control"] == "no-store"
    assert identity.calls == [("refresh", ("refresh-opaque", CID))]


def test_renew_with_an_invalid_refresh_token_is_unauthorized(
    app: Flask, identity: FakeIdentity
) -> None:
    identity.error = rejected(401, "refresh_invalido")

    response = app.test_client().post("/v1/sesiones/renovar", json={"refreshToken": "viejo"})

    assert response.status_code == 401
    assert body(response)["code"] == "refresh_invalido"


@pytest.mark.parametrize(
    "payload", [{}, {"refreshToken": ""}, {"refreshToken": 1}, {"refresh_token": "x"}]
)
def test_invalid_renew_payload_is_rejected(
    app: Flask, identity: FakeIdentity, payload: dict[str, object]
) -> None:
    response = app.test_client().post("/v1/sesiones/renovar", json=payload)

    assert response.status_code == 400
    assert identity.calls == []


def test_renew_outage_is_a_503(app: Flask, identity: FakeIdentity) -> None:
    identity.error = IdentityUnavailableError()

    response = app.test_client().post("/v1/sesiones/renovar", json={"refreshToken": "x"})

    assert response.status_code == 503


def test_current_session_comes_from_the_claims_without_calling_identity(
    app: Flask, identity: FakeIdentity, token: str
) -> None:
    response = app.test_client().get("/v1/sesiones/actual", headers=bearer(token))

    assert response.status_code == 200
    data = body(response)
    assert data["usuario"]["correo"] == "ana@solventa.co"
    assert data["usuario"]["rol"] == "cliente"
    assert data["expiraEn"].endswith("Z")
    assert set(data) == {"usuario", "expiraEn"}
    assert response.headers["Cache-Control"] == "no-store"
    assert identity.calls == []
    assert NOW.year == 2026


def test_missing_or_malformed_authorization_is_unauthorized(app: Flask, token: str) -> None:
    client = app.test_client()
    headers: list[dict[str, str]] = [
        {},
        {"Authorization": "Basic abc"},
        {"Authorization": "Bearer "},
        {"Authorization": token},
    ]

    for header in headers:
        response = client.get("/v1/sesiones/actual", headers=header)
        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == "Bearer"
        assert body(response)["code"] == "sesion_invalida"


def test_bearer_scheme_is_case_insensitive(app: Flask, token: str) -> None:
    response = app.test_client().get(
        "/v1/sesiones/actual", headers={"Authorization": f"bearer {token}"}
    )

    assert response.status_code == 200


@pytest.mark.parametrize(
    "case",
    ["expired", "foreign_signature", "audience", "issuer", "none", "hs256", "mobile", "garbage"],
)
def test_every_invalid_token_is_unauthorized(
    app: Flask, keypair: KeyPair, other_keypair: KeyPair, case: str
) -> None:
    import time

    now = int(time.time())
    tokens = {
        "expired": sign_token(keypair, iat=now - 7200, exp=now - 3600),
        "foreign_signature": sign_token(other_keypair),
        "audience": sign_token(keypair, aud="otra-api"),
        "issuer": sign_token(keypair, iss="https://intruso.test"),
        "none": forge_unsigned_token(keypair.kid),
        "hs256": forge_hs256_token(keypair.public_pem, keypair.kid),
        "mobile": sign_token(keypair, client_id="movil"),
        "garbage": "abc.def.ghi",
    }

    response = app.test_client().get("/v1/sesiones/actual", headers=bearer(tokens[case]))

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert body(response)["code"] == "sesion_invalida"


def test_unknown_kid_triggers_a_jwks_reload(
    app: Flask, jwks: FakeJwks, keypair: KeyPair, token: str
) -> None:
    from support import make_keypair

    client = app.test_client()
    assert client.get("/v1/sesiones/actual", headers=bearer(token)).status_code == 200
    rotated = make_keypair("kid-rotada")
    jwks.keys.append(rotated)

    # Dentro del intervalo mínimo no se recarga: el token con kid nuevo se rechaza.
    assert client.get("/v1/sesiones/actual", headers=bearer(sign_token(rotated))).status_code == 401
    assert jwks.calls == 1


def test_jwks_outage_is_a_503_not_a_401(app: Flask, jwks: FakeJwks, token: str) -> None:
    jwks.failing = True

    response = app.test_client().get("/v1/sesiones/actual", headers=bearer(token))

    assert response.status_code == 503
    assert body(response)["code"] == "servicio_no_disponible"


def test_logout_revokes_the_refresh_token_when_sent(
    app: Flask, identity: FakeIdentity, token: str
) -> None:
    response = app.test_client().delete(
        "/v1/sesiones/actual",
        json={"refreshToken": "refresh-opaque"},
        headers={**bearer(token), "X-Correlation-ID": CID},
    )

    assert response.status_code == 204
    assert response.headers["Cache-Control"] == "no-store"
    assert identity.calls == [("revoke", ("refresh-opaque", CID))]


def test_logout_without_body_does_not_call_identity(
    app: Flask, identity: FakeIdentity, token: str
) -> None:
    response = app.test_client().delete("/v1/sesiones/actual", headers=bearer(token))

    assert response.status_code == 204
    assert identity.calls == []


def test_logout_requires_a_valid_access_token(app: Flask, identity: FakeIdentity) -> None:
    response = app.test_client().delete("/v1/sesiones/actual", json={"refreshToken": "x"})

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert identity.calls == []


def test_logout_with_an_invalid_body_is_rejected(
    app: Flask, identity: FakeIdentity, token: str
) -> None:
    response = app.test_client().delete(
        "/v1/sesiones/actual", json={"refreshToken": 123}, headers=bearer(token)
    )

    assert response.status_code == 400
    assert identity.calls == []


def test_logout_outage_while_revoking_is_a_503(
    app: Flask, identity: FakeIdentity, token: str
) -> None:
    identity.error = IdentityUnavailableError()

    response = app.test_client().delete(
        "/v1/sesiones/actual", json={"refreshToken": "x"}, headers=bearer(token)
    )

    assert response.status_code == 503


def test_ping_needs_no_credentials(app: Flask) -> None:
    response = app.test_client().get("/ping")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok", "service": "bff-web"}
