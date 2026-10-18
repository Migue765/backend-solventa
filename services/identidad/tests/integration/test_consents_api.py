from collections.abc import Callable
from datetime import datetime, timedelta

from flask import Flask
from identidad.infrastructure.persistence.models import ConsentEventModel, ConsentModel
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

TOKEN = "token-interno-de-prueba"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def register(app: Flask) -> str:
    response = app.test_client().post(
        "/v1/usuarios",
        json={
            "nombre": "Ana",
            "correo": "ana@solventa.co",
            "contrasena": "Solventa123!",
        },
    )
    assert response.status_code == 201
    return str(response.get_json()["usuario"]["id"])


def test_persisted_consent_versions_and_outbox_are_atomic(
    make_app: Callable[..., Flask], database_url: str
) -> None:
    app = make_app(internal_api_token=TOKEN)
    user = register(app)
    path = f"/v1/clientes/{user}/consentimiento-perfilamiento"
    client = app.test_client()
    assert client.get(path, headers=HEADERS).status_code == 404
    consent = client.put(path, headers=HEADERS, json={"autorizado": True, "version": 0})
    assert consent.status_code == 200
    data = consent.get_json()
    assert data["estado"] == "autorizado"
    assert datetime.fromisoformat(data["vigenteHasta"]) - datetime.fromisoformat(
        data["efectivaEn"]
    ) == timedelta(days=30)
    restarted = make_app(internal_api_token=TOKEN).test_client()
    assert restarted.get(path, headers=HEADERS).get_json() == data
    assert (
        restarted.put(
            path,
            headers=HEADERS,
            json={
                "autorizado": True,
                "version": 0,
            },
        ).status_code
        == 409
    )
    revoked = restarted.put(path, headers=HEADERS, json={"autorizado": False, "version": 1})
    assert revoked.status_code == 200
    assert revoked.get_json()["estado"] == "revocado"
    assert revoked.get_json()["consentimientoId"] == data["consentimientoId"]
    assert revoked.get_json()["version"] == 2
    with Session(create_engine(database_url)) as session:
        assert session.scalar(select(func.count()).select_from(ConsentModel)) == 2
        assert session.scalar(select(func.count()).select_from(ConsentEventModel)) == 2


def test_internal_credential_and_strict_payload_are_required(
    make_app: Callable[..., Flask],
) -> None:
    app = make_app(internal_api_token=TOKEN)
    path = f"/v1/clientes/{register(app)}/consentimiento-perfilamiento"
    client = app.test_client()
    assert client.get(path).status_code == 401
    for payload in [
        {"autorizado": "true", "version": 0},
        {"autorizado": True, "version": True},
        {"autorizado": True, "version": -1},
        {"autorizado": True, "version": 0, "clienteId": "ajeno"},
    ]:
        assert client.put(path, json=payload, headers=HEADERS).status_code == 400


def test_missing_internal_configuration_does_not_authorize(
    make_app: Callable[..., Flask],
) -> None:
    app = make_app()
    path = f"/v1/clientes/{register(app)}/consentimiento-perfilamiento"
    assert app.test_client().get(path, headers=HEADERS).status_code == 503
