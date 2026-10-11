from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
from bff_web.app import create_app
from bff_web.config import Config
from bff_web.domain.auth import User
from bff_web.infrastructure.development_credentials import DevelopmentCredential
from bff_web.infrastructure.passwords import hash_password
from flask import Flask
from werkzeug.test import TestResponse

PASSWORD = "Solventa123!"


@pytest.fixture
def app() -> Flask:
    user = User(uuid4(), "Cliente Solventa", "cliente@solventa.co", "cliente")
    return create_app(
        Config(
            credentials=(
                DevelopmentCredential(
                    user,
                    hash_password(PASSWORD, salt=b"0123456789abcdef"),
                ),
            ),
            session_cookie_secure=False,
            session_duration=timedelta(minutes=30),
            max_attempts=2,
        )
    )


def test_login_current_session_and_logout(app: Flask) -> None:
    client = app.test_client()

    login_response = client.post(
        "/v1/sesiones",
        json={"correo": "cliente@solventa.co", "contrasena": PASSWORD},
    )

    assert login_response.status_code == 200
    assert response_json(login_response)["usuario"]["correo"] == "cliente@solventa.co"
    assert "HttpOnly" in login_response.headers["Set-Cookie"]
    assert "SameSite=Lax" in login_response.headers["Set-Cookie"]

    current_response = client.get("/v1/sesiones/actual")
    assert current_response.status_code == 200
    assert response_json(current_response)["usuario"]["rol"] == "cliente"

    logout_response = client.delete("/v1/sesiones/actual")
    assert logout_response.status_code == 204
    assert client.get("/v1/sesiones/actual").status_code == 401


def test_invalid_credentials_do_not_reveal_user_existence(app: Flask) -> None:
    client = app.test_client()

    unknown_user = client.post(
        "/v1/sesiones",
        json={"correo": "desconocido@solventa.co", "contrasena": "incorrecta"},
    )
    known_user = client.post(
        "/v1/sesiones",
        json={"correo": "cliente@solventa.co", "contrasena": "incorrecta"},
    )

    assert unknown_user.status_code == 401
    assert known_user.status_code == 401
    assert (
        response_json(unknown_user)["code"]
        == response_json(known_user)["code"]
        == "credenciales_invalidas"
    )


def test_duplicate_failures_are_rate_limited(app: Flask) -> None:
    client = app.test_client()
    payload = {"correo": "cliente@solventa.co", "contrasena": "incorrecta"}

    assert client.post("/v1/sesiones", json=payload).status_code == 401
    assert client.post("/v1/sesiones", json=payload).status_code == 401
    response = client.post("/v1/sesiones", json=payload)

    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) > 0


def test_invalid_payload_returns_problem_details(app: Flask) -> None:
    response = app.test_client().post(
        "/v1/sesiones",
        json={"correo": "no-es-correo", "contrasena": "corta"},
    )

    assert response.status_code == 400
    assert response.content_type == "application/problem+json"
    assert response_json(response)["code"] == "solicitud_invalida"
    assert response_json(response)["correlationId"]


def test_logout_requires_a_valid_session(app: Flask) -> None:
    response = app.test_client().delete("/v1/sesiones/actual")

    assert response.status_code == 401
    assert response_json(response)["code"] == "sesion_invalida"


def test_non_json_request_returns_problem_details(app: Flask) -> None:
    response = app.test_client().post(
        "/v1/sesiones",
        data="correo=cliente",
        content_type="application/x-www-form-urlencoded",
    )

    assert response.status_code == 400
    assert response.content_type == "application/problem+json"


def response_json(response: TestResponse) -> dict[str, Any]:
    payload = response.get_json()
    assert isinstance(payload, dict)
    return payload
