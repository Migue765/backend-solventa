import hashlib
import threading
from typing import Any

from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy import create_engine, text
from werkzeug.test import TestResponse

PASSWORD = "Solventa123!"


def body(response: TestResponse) -> dict[str, Any]:
    data = response.get_json()
    assert isinstance(data, dict)
    return data


def login(client: FlaskClient, client_id: str = "web") -> dict[str, Any]:
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
    return body(response)


def refresh(client: FlaskClient, token: str, client_id: str = "web") -> TestResponse:
    return client.post(
        "/oauth/token",
        json={"grant_type": "refresh_token", "client_id": client_id, "refresh_token": token},
    )


def test_refresh_rotates_the_token_and_returns_a_full_payload(client: FlaskClient) -> None:
    first = login(client)

    response = refresh(client, first["refresh_token"])

    assert response.status_code == 200
    second = body(response)
    assert set(second) == set(first)
    assert second["refresh_token"] != first["refresh_token"]
    assert second["access_token"] != first["access_token"]
    assert second["expires_in"] == 3600
    assert second["usuario"] == first["usuario"]
    assert refresh(client, second["refresh_token"]).status_code == 200


def test_reusing_a_rotated_token_revokes_the_whole_family(client: FlaskClient) -> None:
    first = login(client)
    second = body(refresh(client, first["refresh_token"]))

    reuse = refresh(client, first["refresh_token"])

    assert reuse.status_code == 401
    assert body(reuse)["code"] == "refresh_invalido"
    assert refresh(client, second["refresh_token"]).status_code == 401


def test_other_families_are_not_affected_by_a_reuse(client: FlaskClient) -> None:
    first = login(client)
    other_device = login(client, client_id="movil")
    refresh(client, first["refresh_token"])

    refresh(client, first["refresh_token"])

    assert refresh(client, other_device["refresh_token"], client_id="movil").status_code == 200


def test_refresh_from_another_client_is_invalid_and_keeps_the_token(client: FlaskClient) -> None:
    first = login(client, client_id="web")

    wrong = refresh(client, first["refresh_token"], client_id="movil")

    assert wrong.status_code == 401
    assert body(wrong)["code"] == "refresh_invalido"
    assert refresh(client, first["refresh_token"], client_id="web").status_code == 200


def test_unknown_refresh_token_is_invalid(client: FlaskClient) -> None:
    response = refresh(client, "desconocido")

    assert response.status_code == 401
    assert body(response)["code"] == "refresh_invalido"


def test_web_refresh_expires_after_eight_hours(client: FlaskClient, clock: Any) -> None:
    first = login(client)
    clock.advance(hours=7, minutes=59)
    second = body(refresh(client, first["refresh_token"]))
    assert second["refresh_expires_in"] == 8 * 3600

    clock.advance(hours=8, seconds=1)

    assert refresh(client, second["refresh_token"]).status_code == 401


def test_movil_refresh_lasts_thirty_days(client: FlaskClient, clock: Any) -> None:
    first = login(client, client_id="movil")

    clock.advance(days=29)
    still_valid = refresh(client, first["refresh_token"], client_id="movil")
    assert still_valid.status_code == 200

    clock.advance(days=30, seconds=1)
    expired = refresh(client, body(still_valid)["refresh_token"], client_id="movil")
    assert expired.status_code == 401


def test_only_the_hash_of_the_refresh_token_is_stored(
    client: FlaskClient, database_url: str
) -> None:
    token = login(client)["refresh_token"]

    with create_engine(database_url).connect() as connection:
        rows = [
            str(row[0]) for row in connection.execute(text("select token_hash from refresh_tokens"))
        ]

    assert rows == [hashlib.sha256(token.encode()).hexdigest()]
    assert token not in rows


def test_revoke_invalidates_the_refresh_token(client: FlaskClient) -> None:
    token = login(client)["refresh_token"]

    response = client.post("/oauth/revoke", json={"refresh_token": token})

    assert response.status_code == 204
    assert response.get_data() == b""
    assert refresh(client, token).status_code == 401


def test_revoke_is_idempotent_and_does_not_reveal_existence(client: FlaskClient) -> None:
    token = login(client)["refresh_token"]

    statuses = [
        client.post("/oauth/revoke", json={"refresh_token": value}).status_code
        for value in (token, token, "desconocido")
    ]

    assert statuses == [204, 204, 204]


def test_revoke_accepts_form_encoded_bodies(client: FlaskClient) -> None:
    token = login(client)["refresh_token"]

    response = client.post("/oauth/revoke", data={"refresh_token": token})

    assert response.status_code == 204
    assert refresh(client, token).status_code == 401


def test_revoking_any_token_of_the_family_revokes_the_newest(client: FlaskClient) -> None:
    first = login(client)
    second = body(refresh(client, first["refresh_token"]))

    assert (
        client.post("/oauth/revoke", json={"refresh_token": first["refresh_token"]}).status_code
        == 204
    )

    assert refresh(client, second["refresh_token"]).status_code == 401


def test_revoke_without_a_token_is_a_bad_request(client: FlaskClient) -> None:
    response = client.post("/oauth/revoke", json={})

    assert response.status_code == 400
    assert body(response)["code"] == "solicitud_invalida"


def test_simultaneous_use_of_one_refresh_token_never_yields_two_valid_tokens(
    app: Flask,
) -> None:
    client = app.test_client()
    first = login(client)
    barrier = threading.Barrier(2)
    responses: list[TestResponse] = []

    def use() -> None:
        worker = app.test_client()
        barrier.wait()
        responses.append(refresh(worker, first["refresh_token"]))

    threads = [threading.Thread(target=use) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    statuses = sorted(response.status_code for response in responses)
    assert statuses == [200, 401]
    winner = next(body(response) for response in responses if response.status_code == 200)
    assert refresh(client, winner["refresh_token"]).status_code == 401
