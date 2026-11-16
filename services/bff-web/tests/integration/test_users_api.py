from dataclasses import replace
from typing import Any

import pytest
from bff_web.domain.errors import IdentityUnavailableError
from flask import Flask
from support import NOW, USER, FakeIdentity, rejected
from werkzeug.test import TestResponse

NEW_USER = {"nombre": "Ana Cliente", "correo": "Ana@Solventa.co", "contrasena": "Solventa123!"}
CID = "7a7f3a9e-1c1e-4f4a-9a53-0b6b2c5f9d11"


def body(response: TestResponse) -> dict[str, Any]:
    data = response.get_json()
    assert isinstance(data, dict)
    return data


def test_registration_is_proxied_to_identity(app: Flask, identity: FakeIdentity) -> None:
    response = app.test_client().post(
        "/v1/usuarios", json=NEW_USER, headers={"X-Correlation-ID": CID}
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
    response = app.test_client().post("/v1/usuarios", json=payload)

    assert response.status_code == 400
    assert body(response)["code"] == "solicitud_invalida"
    assert identity.calls == []


def test_non_json_registration_is_a_problem_response(app: Flask) -> None:
    response = app.test_client().post("/v1/usuarios", data="a=b")

    assert response.status_code == 400
    assert response.content_type == "application/problem+json"


def test_duplicate_email_is_a_conflict(app: Flask, identity: FakeIdentity) -> None:
    identity.error = rejected(409, "correo_ya_registrado")

    response = app.test_client().post("/v1/usuarios", json=NEW_USER)

    assert response.status_code == 409
    assert body(response)["code"] == "correo_ya_registrado"
    assert body(response)["correlationId"]


def test_rate_limit_keeps_retry_after(app: Flask, identity: FakeIdentity) -> None:
    identity.error = rejected(429, "demasiados_intentos", 120)

    response = app.test_client().post("/v1/usuarios", json=NEW_USER)

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "120"


def test_identity_validation_error_is_translated(app: Flask, identity: FakeIdentity) -> None:
    identity.error = rejected(400, "solicitud_invalida")

    assert app.test_client().post("/v1/usuarios", json=NEW_USER).status_code == 400


def test_identity_outage_is_a_503(app: Flask, identity: FakeIdentity) -> None:
    identity.error = IdentityUnavailableError()

    response = app.test_client().post("/v1/usuarios", json=NEW_USER)

    assert response.status_code == 503
    assert body(response)["code"] == "servicio_no_disponible"


def encrypted_registration() -> dict[str, object]:
    return {
        "nombre": "Ana Cliente",
        "correo": "ana@solventa.co",
        "credencialCifrada": "synthetic-envelope",
        "tipoDocumento": "cc",
        "numeroDocumento": "1234567890",
        "telefono": "+573001234567",
        "fechaNacimiento": "1990-01-01",
        "consentimiento": {"autorizado": False, "versionTerminos": "1.0"},
    }


def test_encrypted_registration_forwards_fields_without_auto_login(
    app: Flask, identity: FakeIdentity
) -> None:
    app.extensions["bff_config"] = replace(
        app.extensions["bff_config"], encrypted_login_enabled=True
    )
    client = app.test_client()
    assert client.get("/v1/usuarios/desafio").status_code == 200
    response = client.post("/v1/usuarios", json=encrypted_registration())
    assert response.status_code == 201
    assert set(body(response)) == {"usuario"}
    assert body(response)["usuario"]["rol"] == "cliente"
    assert identity.calls[-1][0] == "encrypted_register"
    forwarded = identity.calls[-1][1][0]
    assert forwarded == encrypted_registration()
    assert client.post("/v1/usuarios", json=NEW_USER).status_code == 400


@pytest.mark.parametrize("invalid", ["schema", "expired", "short-key"])
def test_registration_challenge_failure_never_falls_back(
    app: Flask, identity: FakeIdentity, monkeypatch: pytest.MonkeyPatch, invalid: str
) -> None:
    app.extensions["bff_config"] = replace(
        app.extensions["bff_config"], encrypted_login_enabled=True
    )
    payload = FakeIdentity().registration_challenge(CID)
    if invalid == "schema":
        payload = {"clavePublica": {}}
    elif invalid == "expired":
        payload["expiraEn"] = NOW.isoformat()
    else:
        key = payload["clavePublica"]
        assert isinstance(key, dict)
        key["n"] = "test"
    monkeypatch.setattr(identity, "registration_challenge", lambda _: payload)
    assert app.test_client().get("/v1/usuarios/desafio").status_code == 503
    assert identity.calls == []
