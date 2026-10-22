from typing import Any

import jwt
from flask import Flask
from flask.testing import FlaskClient

PASSWORD = "Solventa123!"
INTERNAL_TOKEN = "internal-test-token"


def _register_client(client: FlaskClient) -> str:
    response = client.post(
        "/v1/usuarios",
        json={
            "nombre": "Ana Cliente",
            "correo": "ana@solventa.co",
            "contrasena": PASSWORD,
        },
    )
    assert response.status_code == 201
    return response.get_json()["usuario"]["id"]


def test_advisor_login_includes_authorized_partners(
    make_app: Any,
) -> None:
    app: Flask = make_app(internal_api_token=INTERNAL_TOKEN)
    runner = app.test_cli_runner()
    created = runner.invoke(
        args=[
            "create-advisor",
            "--name",
            "Laura Asesora",
            "--email",
            "laura@banco.co",
            "--password",
            PASSWORD,
            "--partner-id",
            "BANCO-001",
            "--partner-name",
            "Banco Aliado",
        ]
    )
    assert created.exit_code == 0

    response = app.test_client().post(
        "/oauth/token",
        json={
            "grant_type": "password",
            "client_id": "web",
            "username": "laura@banco.co",
            "password": PASSWORD,
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["scope"] == "asesor"
    assert payload["usuario"]["rol"] == "asesor"
    assert payload["usuario"]["socios"] == ["BANCO-001"]
    claims = jwt.decode(payload["access_token"], options={"verify_signature": False})
    assert claims["scope"] == "asesor"
    assert claims["socios"] == ["BANCO-001"]


def test_internal_client_catalog_requires_service_credential(make_app: Any) -> None:
    app: Flask = make_app(internal_api_token=INTERNAL_TOKEN)
    client = app.test_client()
    client_id = _register_client(client)

    unauthorized = client.get("/v1/clientes")
    response = client.get(
        "/v1/clientes",
        headers={"Authorization": f"Bearer {INTERNAL_TOKEN}"},
    )

    assert unauthorized.status_code == 401
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.get_json() == {
        "clientes": [
            {
                "clienteId": client_id,
                "nombre": "Ana Cliente",
                "correo": "ana@solventa.co",
            }
        ]
    }
