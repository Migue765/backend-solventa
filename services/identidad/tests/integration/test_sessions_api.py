from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid4

import jwt
from flask import Flask
from flask.testing import FlaskClient

PASSWORD = "Solventa123!"
INTERNAL_TOKEN = "internal-test-token"
INTERNAL = {"Authorization": "Bearer " + INTERNAL_TOKEN}


def _login(client: FlaskClient, client_id: str = "movil") -> dict[str, Any]:
    client.post(
        "/v1/usuarios",
        json={"nombre": "Ana", "correo": "ana@solventa.co", "contrasena": PASSWORD},
    )
    response = client.post(
        "/oauth/token",
        json={
            "grant_type": "password",
            "client_id": client_id,
            "username": "ana@solventa.co",
            "password": PASSWORD,
        },
    )
    assert response.status_code == 200
    tokens: dict[str, Any] = response.get_json()
    return tokens


def _sid(access_token: str) -> str:
    claims = jwt.decode(access_token, options={"verify_signature": False})
    return str(UUID(claims["sid"]))


def _active(client: FlaskClient, sid: str) -> bool:
    response = client.get(f"/v1/sesiones/{sid}", headers=INTERNAL)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.get_json()["sesionId"] == sid
    return bool(response.get_json()["activa"])


def test_access_tokens_share_session_until_logout(make_app: Callable[..., Flask]) -> None:
    client = make_app(internal_api_token=INTERNAL_TOKEN).test_client()
    first = _login(client)
    other_device = _login(client)
    sid = _sid(first["access_token"])
    assert sid != _sid(other_device["access_token"])
    assert _active(client, sid)

    rotated = client.post(
        "/oauth/token",
        json={
            "grant_type": "refresh_token",
            "client_id": "movil",
            "refresh_token": first["refresh_token"],
        },
    ).get_json()
    assert _sid(rotated["access_token"]) == sid
    assert _active(client, sid)

    revoked = client.post("/oauth/revoke", json={"refresh_token": rotated["refresh_token"]})
    assert revoked.status_code == 204
    assert not _active(client, sid)
    assert _active(client, _sid(other_device["access_token"]))


def test_session_expires_with_its_refresh_token(make_app: Callable[..., Flask], clock: Any) -> None:
    client = make_app(internal_api_token=INTERNAL_TOKEN).test_client()
    sid = _sid(_login(client)["access_token"])
    clock.advance(days=31)
    assert not _active(client, sid)
    assert not _active(client, str(uuid4()))


def test_session_state_requires_internal_credential(make_app: Callable[..., Flask]) -> None:
    client = make_app(internal_api_token=INTERNAL_TOKEN).test_client()
    sid = str(uuid4())
    assert client.get(f"/v1/sesiones/{sid}").status_code == 401
    wrong = client.get(f"/v1/sesiones/{sid}", headers={"Authorization": "Bearer otro"})
    assert wrong.status_code == 401
    unconfigured = make_app().test_client()
    assert unconfigured.get(f"/v1/sesiones/{sid}", headers=INTERNAL).status_code == 401
    assert client.get("/v1/sesiones/no-uuid", headers=INTERNAL).status_code == 404
