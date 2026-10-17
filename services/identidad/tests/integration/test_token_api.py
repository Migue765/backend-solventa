import json
from collections.abc import Callable
from typing import Any, cast

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from flask import Flask
from flask.testing import FlaskClient
from werkzeug.test import TestResponse

PASSWORD = "Solventa123!"
ISSUER = "https://api.solventa.test"
CLAIMS = {"iss", "sub", "aud", "iat", "exp", "jti", "scope", "rol", "nombre", "correo", "client_id"}


def body(response: TestResponse) -> dict[str, Any]:
    data = response.get_json()
    assert isinstance(data, dict)
    return data


def register(client: FlaskClient, email: str = "ana@solventa.co") -> None:
    response = client.post(
        "/v1/usuarios", json={"nombre": "Ana Cliente", "correo": email, "contrasena": PASSWORD}
    )
    assert response.status_code == 201


def password_grant(
    client: FlaskClient,
    username: str = "ana@solventa.co",
    password: str = PASSWORD,
    client_id: str = "web",
    **kwargs: Any,
) -> TestResponse:
    return client.post(
        "/oauth/token",
        json={
            "grant_type": "password",
            "client_id": client_id,
            "username": username,
            "password": password,
        },
        **kwargs,
    )


def decode(client: FlaskClient, token: str) -> dict[str, Any]:
    jwk = body(client.get("/.well-known/jwks.json"))["keys"][0]
    public_key = cast(RSAPublicKey, jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(jwk)))
    return jwt.decode(
        token, public_key, algorithms=["RS256"], audience="solventa-api", issuer=ISSUER
    )


def test_password_grant_returns_the_contract_payload(client: FlaskClient) -> None:
    register(client)

    response = password_grant(client)

    assert response.status_code == 200
    data = body(response)
    assert set(data) == {
        "access_token",
        "token_type",
        "expires_in",
        "refresh_token",
        "refresh_expires_in",
        "scope",
        "usuario",
    }
    assert data["token_type"] == "Bearer"
    assert data["expires_in"] == 3600
    assert data["refresh_expires_in"] == 8 * 3600
    assert data["scope"] == "cliente"
    assert data["usuario"]["correo"] == "ana@solventa.co"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Pragma"] == "no-cache"


def test_access_token_is_verifiable_with_the_published_key(client: FlaskClient) -> None:
    register(client)
    data = body(password_grant(client))

    claims = decode(client, data["access_token"])
    header = jwt.get_unverified_header(data["access_token"])

    jwks_kid = body(client.get("/.well-known/jwks.json"))["keys"][0]["kid"]
    assert header == {"alg": "RS256", "typ": "JWT", "kid": jwks_kid}
    assert set(claims) == CLAIMS
    assert claims["iss"] == ISSUER
    assert claims["aud"] == "solventa-api"
    assert claims["sub"] == data["usuario"]["id"]
    assert claims["exp"] - claims["iat"] == 3600
    assert claims["scope"] == "cliente"
    assert claims["rol"] == "cliente"
    assert claims["nombre"] == "Ana Cliente"
    assert claims["correo"] == "ana@solventa.co"
    assert claims["client_id"] == "web"


def test_each_client_gets_its_own_refresh_lifetime_and_claim(client: FlaskClient) -> None:
    register(client)

    web = body(password_grant(client, client_id="web"))
    movil = body(password_grant(client, client_id="movil"))

    assert web["refresh_expires_in"] == 8 * 3600
    assert movil["refresh_expires_in"] == 30 * 86400
    assert decode(client, movil["access_token"])["client_id"] == "movil"


def test_form_encoded_requests_are_accepted(client: FlaskClient) -> None:
    register(client)

    response = client.post(
        "/oauth/token",
        data={
            "grant_type": "password",
            "client_id": "movil",
            "username": "ana@solventa.co",
            "password": PASSWORD,
        },
    )

    assert response.status_code == 200
    assert body(response)["refresh_expires_in"] == 30 * 86400


def test_username_is_case_insensitive(client: FlaskClient) -> None:
    register(client)

    assert password_grant(client, username="ANA@Solventa.CO").status_code == 200


def test_wrong_password_and_unknown_user_are_indistinguishable(client: FlaskClient) -> None:
    register(client)

    wrong = password_grant(client, password="Incorrecta1")
    unknown = password_grant(client, username="nadie@solventa.co")

    assert wrong.status_code == unknown.status_code == 401
    wrong_body, unknown_body = body(wrong), body(unknown)
    for key in ("code", "title", "detail", "status", "type"):
        assert wrong_body[key] == unknown_body[key]
    assert wrong_body["code"] == "credenciales_invalidas"
    assert wrong.content_type == "application/problem+json"


def test_login_attempts_are_limited_per_email_regardless_of_ip(client: FlaskClient) -> None:
    register(client)
    for index in range(3):
        failed = password_grant(
            client, password="Incorrecta1", environ_overrides={"REMOTE_ADDR": f"10.0.0.{index}"}
        )
        assert failed.status_code == 401

    blocked = password_grant(client, environ_overrides={"REMOTE_ADDR": "10.9.9.9"})
    other_email = password_grant(client, username="otro@solventa.co")

    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) >= 1
    assert body(blocked)["code"] == "demasiados_intentos"
    assert other_email.status_code == 401


def test_successful_login_resets_the_failure_counter(client: FlaskClient) -> None:
    register(client)
    for _ in range(2):
        assert password_grant(client, password="Incorrecta1").status_code == 401

    assert password_grant(client).status_code == 200

    for _ in range(2):
        assert password_grant(client, password="Incorrecta1").status_code == 401
    assert password_grant(client).status_code == 200


def test_attempts_window_expires_in_memory_limiter(client: FlaskClient, clock: Any) -> None:
    register(client)
    for _ in range(3):
        password_grant(client, password="Incorrecta1")
    assert password_grant(client).status_code == 429

    clock.advance(seconds=301)

    assert password_grant(client).status_code == 200


@pytest.mark.parametrize(
    "payload",
    [
        {"grant_type": "password", "username": "ana@solventa.co", "password": PASSWORD},
        {
            "grant_type": "password",
            "client_id": "otro",
            "username": "ana@solventa.co",
            "password": PASSWORD,
        },
        {
            "grant_type": "client_credentials",
            "client_id": "web",
            "username": "ana@solventa.co",
            "password": PASSWORD,
        },
        {"grant_type": "password", "client_id": "web", "username": "ana@solventa.co"},
        {"grant_type": "password", "client_id": "web", "password": PASSWORD},
        {"grant_type": "password", "client_id": "web", "username": "no-es-correo", "password": "x"},
        {"grant_type": "refresh_token", "client_id": "web"},
        {},
    ],
)
def test_invalid_token_requests_are_rejected(client: FlaskClient, payload: dict[str, str]) -> None:
    response = client.post("/oauth/token", json=payload)

    assert response.status_code == 400
    assert body(response)["code"] == "solicitud_invalida"


def test_unsupported_content_type_and_array_body_are_rejected(client: FlaskClient) -> None:
    text_plain = client.post("/oauth/token", data="x", content_type="text/plain")
    array = client.post("/oauth/token", json=["a"])

    assert text_plain.status_code == array.status_code == 400
    assert text_plain.content_type == "application/problem+json"


def test_unavailable_redis_returns_503_on_token(
    make_app: Callable[..., Flask], monkeypatch: pytest.MonkeyPatch
) -> None:
    from redis.exceptions import ConnectionError as RedisConnectionError

    class BrokenRedis:
        def __getattr__(self, name: str) -> Any:
            def fail(*_: Any, **__: Any) -> None:
                raise RedisConnectionError("caído")

            return fail

    monkeypatch.setattr("identidad.app.Redis.from_url", lambda *_, **__: BrokenRedis())
    client = make_app(redis_url="redis://fake").test_client()

    response = password_grant(client)

    assert response.status_code == 503
    assert body(response)["code"] == "servicio_no_disponible"
