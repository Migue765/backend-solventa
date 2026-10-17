from typing import Any

import pytest
from bff_movil.domain.errors import IdentityUnavailableError
from flask import Flask
from support import USER, FakeIdentity, rejected
from werkzeug.test import TestResponse

NEW_USER = {"nombre": "Ana Cliente", "correo": "Ana@Solventa.co", "contrasena": "Solventa123!"}
CID = "7a7f3a9e-1c1e-4f4a-9a53-0b6b2c5f9d11"


def body(response: TestResponse) -> dict[str, Any]:
    data = response.get_json()
    assert isinstance(data, dict)
    return data


def test_registration_is_proxied_to_identity(app: Flask, identity: FakeIdentity) -> None:
    response = app.test_client().post(
        "/movil/v1/usuarios", json=NEW_USER, headers={"X-Correlation-ID": CID}
    )

    assert response.status_code == 201
    assert body(response) == {
        "usuario": {
            "id": str(USER.user_id),
            "nombre": "Ana Cliente",
            "correo": "ana@solventa.co",
            "rol": "cliente",
        }
    }
    assert response.headers["Cache-Control"] == "no-store"
    assert "Set-Cookie" not in response.headers
    assert "contrasena" not in response.get_data(as_text=True)
    assert identity.calls == [("register", ("Ana Cliente", "Ana@solventa.co", "Solventa123!", CID))]


@pytest.mark.parametrize(
    "payload",
    [
        {**NEW_USER, "contrasena": "corta"},
        {**NEW_USER, "correo": "no-es-correo"},
        {**NEW_USER, "nombre": ""},
        {**NEW_USER, "rol": "admin"},
        {"correo": "ana@solventa.co"},
    ],
)
def test_invalid_registration_never_reaches_identity(
    app: Flask, identity: FakeIdentity, payload: dict[str, str]
) -> None:
    response = app.test_client().post("/movil/v1/usuarios", json=payload)

    assert response.status_code == 400
    assert body(response)["code"] == "solicitud_invalida"
    assert identity.calls == []


def test_non_json_registration_is_a_problem_response(app: Flask) -> None:
    response = app.test_client().post("/movil/v1/usuarios", data="a=b")

    assert response.status_code == 400
    assert response.content_type == "application/problem+json"


def test_duplicate_email_is_a_conflict(app: Flask, identity: FakeIdentity) -> None:
    identity.error = rejected(409, "correo_ya_registrado")

    response = app.test_client().post("/movil/v1/usuarios", json=NEW_USER)

    assert response.status_code == 409
    assert body(response)["code"] == "correo_ya_registrado"
    assert body(response)["correlationId"]


def test_rate_limit_keeps_retry_after(app: Flask, identity: FakeIdentity) -> None:
    identity.error = rejected(429, "demasiados_intentos", 120)

    response = app.test_client().post("/movil/v1/usuarios", json=NEW_USER)

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "120"


def test_identity_validation_error_is_translated(app: Flask, identity: FakeIdentity) -> None:
    identity.error = rejected(400, "solicitud_invalida")

    assert app.test_client().post("/movil/v1/usuarios", json=NEW_USER).status_code == 400


def test_identity_outage_is_a_503(app: Flask, identity: FakeIdentity) -> None:
    identity.error = IdentityUnavailableError()

    response = app.test_client().post("/movil/v1/usuarios", json=NEW_USER)

    assert response.status_code == 503
    assert body(response)["code"] == "servicio_no_disponible"
