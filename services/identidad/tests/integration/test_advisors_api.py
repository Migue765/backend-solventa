from typing import Any
from uuid import uuid4

import jwt
import pytest
from flask import Flask
from flask.testing import FlaskClient
from identidad.infrastructure.persistence.models import SocioModel

PASSWORD = "Solventa123!"
INTERNAL_TOKEN = "internal-test-token"


def _register_client(client: FlaskClient, with_document: bool = False) -> str:
    document = (
        {
            "tipoDocumento": "cc",
            "numeroDocumento": "1012345678",
            "telefono": "+573001234567",
            "fechaNacimiento": "1985-02-10",
        }
        if with_document
        else {}
    )
    response = client.post(
        "/v1/usuarios",
        json={
            "nombre": "Ana Cliente",
            "correo": "ana@solventa.co",
            "contrasena": PASSWORD,
            **document,
        },
    )
    assert response.status_code == 201
    payload = response.get_json()
    assert isinstance(payload, dict)
    user = payload.get("usuario")
    assert isinstance(user, dict)
    client_id = user.get("id")
    assert isinstance(client_id, str)
    return client_id


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


def test_internal_client_lookup_by_document_is_exact_and_authorized(make_app: Any) -> None:
    app: Flask = make_app(internal_api_token=INTERNAL_TOKEN)
    client = app.test_client()
    client_id = _register_client(client, with_document=True)
    headers = {"Authorization": f"Bearer {INTERNAL_TOKEN}"}

    unauthorized = client.get("/v1/clientes?tipoDocumento=cc&numeroDocumento=1012345678")
    found = client.get(
        "/v1/clientes?tipoDocumento=cc&numeroDocumento=1012345678",
        headers=headers,
    )
    missing = client.get(
        "/v1/clientes?tipoDocumento=cc&numeroDocumento=1099999999",
        headers=headers,
    )

    assert unauthorized.status_code == 401
    assert found.status_code == 200
    assert found.get_json() == {
        "clienteId": client_id,
        "nombre": "Ana Cliente",
        "correo": "ana@solventa.co",
        "tipoDocumento": "cc",
        "numeroDocumento": "1012345678",
        "telefono": "+573001234567",
        "fechaNacimiento": "1985-02-10",
    }
    assert missing.status_code == 404
    assert missing.get_json()["code"] == "cliente_no_encontrado"


def test_partner_user_has_distinct_role_and_organization_after_refresh(make_app: Any) -> None:
    app: Flask = make_app()
    created = app.test_cli_runner().invoke(
        args=[
            "create-partner-user",
            "--name",
            "Operador Socio",
            "--email",
            "socio@banco.co",
            "--partner-id",
            "BANCO-002",
            "--partner-name",
            "Banco Dos",
        ],
        input=f"{PASSWORD}\n{PASSWORD}\n",
    )
    assert created.exit_code == 0, created.output
    client = app.test_client()
    login = client.post(
        "/oauth/token",
        json={
            "grant_type": "password",
            "client_id": "web",
            "username": "socio@banco.co",
            "password": PASSWORD,
        },
    )
    assert login.status_code == 200
    tokens = login.get_json()
    assert tokens["usuario"]["rol"] == tokens["scope"] == "socio"
    assert tokens["usuario"]["socios"] == ["BANCO-002"]
    refresh = client.post(
        "/oauth/token",
        json={
            "grant_type": "refresh_token",
            "client_id": "web",
            "refresh_token": tokens["refresh_token"],
        },
    )
    assert refresh.status_code == 200
    claims = jwt.decode(refresh.get_json()["access_token"], options={"verify_signature": False})
    assert claims["rol"] == claims["scope"] == "socio"
    assert claims["socios"] == ["BANCO-002"]
    public = client.post(
        "/v1/usuarios",
        json={
            "nombre": "Elevado",
            "correo": "elevado@banco.co",
            "contrasena": PASSWORD,
            "rol": "socio",
        },
    )
    assert public.status_code == 400


@pytest.mark.parametrize("role", ["asesor", "socio"])
def test_internal_authorization_uses_current_role_and_active_membership(
    make_app: Any, role: str
) -> None:
    app: Flask = make_app(internal_api_token=INTERNAL_TOKEN)
    created = app.test_cli_runner().invoke(
        args=[
            "create-advisor" if role == "asesor" else "create-partner-user",
            "--name",
            "Actor de prueba",
            "--email",
            f"{role}@hu44.example",
            "--password",
            PASSWORD,
            "--partner-id",
            "BANCO-HU44",
            "--partner-name",
            "Socio de prueba",
        ]
    )
    assert created.exit_code == 0, created.output
    actor_id = created.output.strip()
    path = f"/v1/autorizaciones/socios/BANCO-HU44/usuarios/{actor_id}?rol={role}"
    client = app.test_client()
    headers = {"Authorization": f"Bearer {INTERNAL_TOKEN}"}
    assert client.get(path).status_code == 401
    authorized = client.get(path, headers=headers)
    assert authorized.status_code == 200
    assert authorized.headers["Cache-Control"] == "no-store"
    assert authorized.get_json() == {
        "actorId": actor_id,
        "socioId": "BANCO-HU44",
        "rol": role,
        "autorizado": True,
    }
    assert (
        client.get(path.replace("BANCO-HU44", "BANCO-AJENO"), headers=headers).get_json()[
            "autorizado"
        ]
        is False
    )
    assert (
        client.get(path.replace(actor_id, str(uuid4())), headers=headers).get_json()["autorizado"]
        is False
    )
    assert (
        client.get(path.replace(f"rol={role}", "rol=cliente"), headers=headers).status_code == 400
    )
    repository = app.extensions["user_repository"]
    with repository._sessions.begin() as session:
        partner = session.get(SocioModel, "BANCO-HU44")
        partner.activo = False
    assert client.get(path, headers=headers).get_json()["autorizado"] is False
