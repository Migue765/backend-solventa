from collections.abc import Callable
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy import create_engine, text
from werkzeug.test import TestResponse

NEW_USER = {"nombre": "Ana Cliente", "correo": "Ana@Solventa.co", "contrasena": "Solventa123!"}
MASTER_DATA = {"telefono": "+573001234567", "fechaNacimiento": "1985-02-10"}


def body(response: TestResponse) -> dict[str, Any]:
    data = response.get_json()
    assert isinstance(data, dict)
    return data


def test_register_creates_a_client_user(client: FlaskClient, database_url: str) -> None:
    response = client.post("/v1/usuarios", json=NEW_USER)

    assert response.status_code == 201
    user = body(response)["usuario"]
    assert user["correo"] == "ana@solventa.co"
    assert user["nombre"] == "Ana Cliente"
    assert user["rol"] == "cliente"
    assert set(user) == {"id", "nombre", "correo", "rol"}
    assert "contrasena" not in response.get_data(as_text=True)
    assert response.headers["Cache-Control"] == "no-store"

    with create_engine(database_url).connect() as connection:
        stored: str = connection.execute(text("select password_hash from usuarios")).scalar_one()
    assert stored.startswith("pbkdf2_sha256$")
    assert NEW_USER["contrasena"] not in stored


def test_duplicate_email_returns_conflict_ignoring_case(client: FlaskClient) -> None:
    assert client.post("/v1/usuarios", json=NEW_USER).status_code == 201

    duplicate = client.post("/v1/usuarios", json={**NEW_USER, "correo": "ANA@solventa.co"})

    assert duplicate.status_code == 409
    assert body(duplicate)["code"] == "correo_ya_registrado"
    assert duplicate.content_type == "application/problem+json"


def test_registers_normalized_optional_document_and_rejects_duplicates(
    client: FlaskClient,
) -> None:
    created = client.post(
        "/v1/usuarios",
        json={
            **NEW_USER,
            "tipoDocumento": "passport",
            "numeroDocumento": " ab12345 ",
            **MASTER_DATA,
        },
    )
    duplicate = client.post(
        "/v1/usuarios",
        json={
            **NEW_USER,
            "correo": "otra@solventa.co",
            "tipoDocumento": "passport",
            "numeroDocumento": "AB12345",
            **MASTER_DATA,
        },
    )

    assert created.status_code == 201
    assert body(created)["usuario"]["tipoDocumento"] == "passport"
    assert body(created)["usuario"]["numeroDocumento"] == "AB12345"
    assert body(created)["usuario"]["telefono"] == "+573001234567"
    assert body(created)["usuario"]["fechaNacimiento"] == "1985-02-10"
    assert duplicate.status_code == 409
    assert body(duplicate)["code"] == "documento_ya_registrado"


@pytest.mark.parametrize(
    "payload",
    [
        {**NEW_USER, "contrasena": "corta"},
        {**NEW_USER, "contrasena": "x" * 129},
        {**NEW_USER, "correo": "no-es-correo"},
        {**NEW_USER, "nombre": ""},
        {**NEW_USER, "rol": "admin"},
        {**NEW_USER, "tipoDocumento": "cc"},
        {**NEW_USER, "numeroDocumento": "123456789"},
        {**NEW_USER, "tipoDocumento": "cc", "numeroDocumento": "ABC123"},
        {**NEW_USER, "tipoDocumento": "cc", "numeroDocumento": "123456789"},
        {**NEW_USER, **MASTER_DATA},
        {"correo": "ana@solventa.co"},
    ],
)
def test_invalid_registration_is_rejected(client: FlaskClient, payload: dict[str, str]) -> None:
    response = client.post("/v1/usuarios", json=payload)

    assert response.status_code == 400
    assert body(response)["code"] == "solicitud_invalida"


def test_non_json_registration_is_rejected_with_problem_details(client: FlaskClient) -> None:
    response = client.post("/v1/usuarios", data="a=b", content_type="text/plain")

    assert response.status_code == 400
    assert response.content_type == "application/problem+json"
    assert set(body(response)) == {"type", "title", "status", "detail", "code", "correlationId"}


def test_correlation_id_is_echoed_or_generated(client: FlaskClient) -> None:
    given = "6b1f5c1e-8c1f-4c43-9f3e-0f2f8f4a9b11"

    echoed = client.get("/ping", headers={"X-Correlation-ID": given})
    generated = client.get("/ping", headers={"X-Correlation-ID": "no-es-uuid"})

    assert echoed.headers["X-Correlation-ID"] == given
    assert generated.headers["X-Correlation-ID"] != "no-es-uuid"


def test_registration_attempts_are_limited_per_email(client: FlaskClient) -> None:
    for _ in range(3):
        client.post("/v1/usuarios", json=NEW_USER)

    blocked = client.post("/v1/usuarios", json=NEW_USER)
    other = client.post("/v1/usuarios", json={**NEW_USER, "correo": "otra@solventa.co"})

    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) >= 1
    assert body(blocked)["code"] == "demasiados_intentos"
    assert other.status_code == 201


def test_unavailable_redis_returns_503(
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

    response = client.post("/v1/usuarios", json=NEW_USER)

    assert response.status_code == 503
    assert body(response)["code"] == "servicio_no_disponible"


def test_redis_limiter_is_used_when_configured(
    make_app: Callable[..., Flask], monkeypatch: pytest.MonkeyPatch
) -> None:
    import fakeredis

    shared = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr("identidad.app.Redis.from_url", lambda *_, **__: shared)
    client = make_app(redis_url="redis://fake").test_client()

    client.post("/v1/usuarios", json=NEW_USER)

    assert shared.exists("identidad:attempts:registro:ana@solventa.co") == 1
