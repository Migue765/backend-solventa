import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from flask import Flask, jsonify
from polizas.infrastructure.models import PolizaModel
from siniestros.app import create_app
from siniestros.domain import Problem
from sqlalchemy import create_engine, text, update
from werkzeug.test import TestResponse

from tests.claims_local_stack import serve, stack


@pytest.fixture
def runtime(tmp_path: Path) -> Iterator[dict[str, Any]]:
    with stack(tmp_path) as running:
        yield running


def headers(runtime: dict[str, Any], index: int = 0, key: str | None = None) -> dict[str, str]:
    result = {"Authorization": f"Bearer {runtime['users'][index]['webToken']}"}
    if key is not None:
        result["Idempotency-Key"] = key
    return result


def post(runtime: dict[str, Any], payload: dict[str, Any], key: str) -> TestResponse:
    app: Flask = runtime["webApp"]
    with app.test_client() as client:
        return client.post("/v1/siniestros", json=payload, headers=headers(runtime, key=key))


def test_registration_replay_list_detail_restart_and_audit(runtime: dict[str, Any]) -> None:
    payload = runtime["users"][0]["claimPayload"]
    key = str(uuid4())
    created = post(runtime, payload, key)
    assert created.status_code == 201
    receipt = created.get_json()
    assert receipt["estado"] == "registrado"
    assert "historial" not in receipt
    assert created.headers["Location"] == f"/v1/siniestros/{receipt['id']}"
    replay = post(runtime, payload, key)
    assert replay.status_code == 200 and replay.get_json() == receipt
    conflict = post(runtime, {**payload, "lugar": "Otro lugar"}, key)
    assert conflict.status_code == 409
    assert conflict.get_json()["code"] == "idempotencia_conflictiva"
    with runtime["webApp"].test_client() as client:
        listing = client.get("/v1/siniestros", headers=headers(runtime)).get_json()["siniestros"]
        assert receipt in listing and len(listing) == 2
        detail = client.get(created.headers["Location"], headers=headers(runtime))
        assert detail.status_code == 200 and detail.get_json() == receipt
        foreign = client.get(created.headers["Location"], headers=headers(runtime, 1))
        assert foreign.status_code == 404
        state = client.get(f"/v1/clientes/me/siniestros/{receipt['id']}", headers=headers(runtime))
        assert state.status_code == 200
        assert state.get_json()["estado"] == "reportado"
        assert len(state.get_json()["historial"]) == 1
        assert "actorId" not in state.get_json()["historial"][0]
    restarted = create_app(runtime["config"])
    repository = restarted.extensions["siniestros_repository"]
    try:
        assert repository.get_claim(runtime["users"][0]["id"], receipt["id"]) == receipt
        with repository.engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT COUNT(*) FROM auditoria_siniestros "
                        "WHERE action='registrar_siniestro'"
                    )
                ).scalar_one()
                >= 1
            )
    finally:
        repository.engine.dispose()


def test_owner_and_confirmed_replay_do_not_follow_current_policy_owner(
    runtime: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = runtime["users"][0]
    claim = user["claim"]
    engine = create_engine(f"sqlite:///{tmp_path / 'polizas.db'}")
    try:
        with engine.begin() as connection:
            connection.execute(
                update(PolizaModel)
                .where(PolizaModel.poliza_id == UUID(claim["polizaId"]))
                .values(cliente_id=UUID(runtime["users"][1]["id"]))
            )
    finally:
        engine.dispose()
    with runtime["webApp"].test_client() as client:
        assert client.get("/v1/siniestros/polizas", headers=headers(runtime)).get_json() == {
            "polizas": []
        }
        transferred = client.get("/v1/siniestros/polizas", headers=headers(runtime, 1)).get_json()
        assert claim["polizaId"] in {policy["id"] for policy in transferred["polizas"]}
    assert post(runtime, user["claimPayload"], user["claimOperation"]).status_code == 200
    assert post(runtime, user["claimPayload"], str(uuid4())).status_code == 404

    def unavailable(*_: object) -> None:
        raise Problem(503, "servicio_no_disponible")

    monkeypatch.setattr("siniestros.repository.policies", unavailable)
    replay = post(runtime, user["claimPayload"], user["claimOperation"])
    assert replay.status_code == 200 and replay.get_json() == claim
    assert post(runtime, user["claimPayload"], str(uuid4())).status_code == 503
    with runtime["webApp"].test_client() as client:
        assert (
            client.get(f"/v1/siniestros/{claim['id']}", headers=headers(runtime)).status_code == 200
        )
        assert (
            client.get(f"/v1/siniestros/{claim['id']}", headers=headers(runtime, 1)).status_code
            == 404
        )


def test_lost_ack_recovers_without_second_record(
    runtime: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = runtime["app"].extensions["siniestros_repository"]
    original = repository.create_claim
    key = str(uuid4())

    def lose_ack(*args: object) -> None:
        original(*args)
        raise Problem(503, "servicio_no_disponible")

    with monkeypatch.context() as patch:
        patch.setattr(repository, "create_claim", lose_ack)
        assert post(runtime, runtime["users"][0]["claimPayload"], key).status_code == 503
    recovered = post(runtime, runtime["users"][0]["claimPayload"], key)
    assert recovered.status_code == 200
    assert len(repository.list_claims(runtime["users"][0]["id"])) == 2


def test_concurrent_retries_create_one_record(runtime: dict[str, Any]) -> None:
    key = str(uuid4())
    with ThreadPoolExecutor(max_workers=4) as executor:
        responses = list(
            executor.map(
                lambda _: post(runtime, runtime["users"][0]["claimPayload"], key),
                range(4),
            )
        )
    assert sorted(response.status_code for response in responses) == [200, 200, 200, 201]
    assert len({response.get_json()["id"] for response in responses}) == 1


@pytest.mark.parametrize(
    ("changes", "status"),
    [
        ({"descripcion": "corta"}, 400),
        ({"tipoEvento": " "}, 400),
        ({"lugar": " "}, 400),
        ({"clienteId": str(uuid4())}, 400),
        ({"fechaEvento": "2020-01-01T10:00:00"}, 400),
        ({"fechaEvento": (datetime.now(UTC) + timedelta(days=1)).isoformat()}, 422),
        ({"fechaEvento": "1900-01-01T00:00:00Z"}, 422),
        ({"polizaId": str(uuid4())}, 404),
    ],
)
def test_invalid_registration_never_persists(
    runtime: dict[str, Any],
    changes: dict[str, Any],
    status: int,
) -> None:
    response = post(runtime, {**runtime["users"][0]["claimPayload"], **changes}, str(uuid4()))
    assert response.status_code == status
    repository = runtime["app"].extensions["siniestros_repository"]
    assert len(repository.list_claims(runtime["users"][0]["id"])) == 1


def test_text_limits_and_multibyte_body_limit(runtime: dict[str, Any]) -> None:
    payload = {
        **runtime["users"][0]["claimPayload"],
        "tipoEvento": "x" * 100,
        "descripcion": "x" * 5000,
        "lugar": "x" * 300,
    }
    assert post(runtime, payload, str(uuid4())).status_code == 201
    payload["descripcion"] = "\U0001f600" * 5000
    with runtime["webApp"].test_client() as client:
        response = client.post(
            "/v1/siniestros",
            data=json.dumps(payload, ensure_ascii=False).encode(),
            content_type="application/json",
            headers=headers(runtime, key=str(uuid4())),
        )
    assert response.status_code == 413
    repository = runtime["app"].extensions["siniestros_repository"]
    assert len(repository.list_claims(runtime["users"][0]["id"])) == 2


@pytest.mark.parametrize("tail", ["", "/polizas", "/11111111-1111-4111-8111-111111111111"])
@pytest.mark.parametrize("body", [{}, {"siniestros": [{}]}, {"polizas": [{}]}])
def test_malformed_success_is_not_presented_as_data(
    runtime: dict[str, Any],
    tail: str,
    body: dict[str, Any],
) -> None:
    upstream = Flask(__name__)
    upstream.add_url_rule("/v1/siniestros" + tail, view_func=lambda: jsonify(body))
    with serve(upstream) as url:
        app = runtime["webApp"]
        config = app.extensions["bff_config"]
        app.extensions["bff_config"] = replace(config, claims_service_url=url)
        try:
            with app.test_client() as client:
                response = client.get("/v1/siniestros" + tail, headers=headers(runtime))
            assert response.status_code == 503
            assert response.get_json()["code"] == "servicio_no_disponible"
        finally:
            app.extensions["bff_config"] = config


def test_unrelated_confirmation_is_rejected_without_logging_payload(
    runtime: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    upstream = Flask(__name__)
    unrelated = {**runtime["users"][0]["claim"], "descripcion": "private-unrelated-description"}
    upstream.add_url_rule(
        "/v1/siniestros", methods=["POST"], view_func=lambda: (jsonify(unrelated), 201)
    )
    with serve(upstream) as url:
        app = runtime["webApp"]
        config = app.extensions["bff_config"]
        app.extensions["bff_config"] = replace(config, claims_service_url=url)
        try:
            assert (
                post(runtime, runtime["users"][0]["claimPayload"], str(uuid4())).status_code == 503
            )
        finally:
            app.extensions["bff_config"] = config
    assert "private-unrelated-description" not in caplog.text
