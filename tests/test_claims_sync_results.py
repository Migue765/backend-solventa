import hashlib
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from siniestros.app import create_app
from siniestros.domain import Problem
from siniestros.models import Audit, Claim, Upload
from sqlalchemy import event, func, select
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError

from tests.claims_local_stack import call, stack
from tests.test_claims_evidence_flow import image_bytes, put, start_upload


@pytest.fixture(scope="module")
def runtime(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Any]]:
    with stack(tmp_path_factory.mktemp("sync-results"), max_count=100) as running:
        yield running


def test_registration_rejection_replays_after_restart_and_binds_payload(runtime: dict) -> None:
    user = runtime["users"][0]
    root = runtime["mobile"] + "/siniestros"
    key = str(uuid4())
    headers = {"Idempotency-Key": key}
    payload = {
        **user["claimPayload"],
        "fechaEvento": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
    }
    status, rejected = call(root, "POST", payload, user["token"], headers)
    assert (status, rejected["code"]) == (422, "fecha_evento_invalida")
    receipt = rejected["ultimoResultado"]
    assert receipt["operacionId"] == key and receipt["httpStatus"] == status
    assert receipt["code"] == rejected["code"]
    assert datetime.fromisoformat(receipt["fecha"]).tzinfo is not None
    restarted = create_app(runtime["config"])
    internal_headers = {
        "Authorization": f"Bearer {runtime['internal']}",
        "X-Cliente-ID": user["id"],
        **headers,
    }
    try:
        replay = restarted.test_client().post(
            "/v1/siniestros", json=payload, headers=internal_headers
        )
        assert replay.status_code == status
        assert replay.get_json()["ultimoResultado"] == receipt
        changed = call(root, "POST", user["claimPayload"], user["token"], headers)
        assert (changed[0], changed[1]["code"]) == (409, "idempotencia_conflictiva")
        assert changed[1]["ultimoResultado"]["httpStatus"] == 409
        again = call(root, "POST", user["claimPayload"], user["token"], headers)
        assert again[1]["ultimoResultado"] == changed[1]["ultimoResultado"]
        assert call(root, "POST", payload, user["token"], headers)[1]["ultimoResultado"] == receipt
        repo = runtime["app"].extensions["siniestros_repository"]
        with repo.sessions() as session:
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(Claim)
                    .where(Claim.owner == user["id"], Claim.operation == key)
                )
                == 0
            )
    finally:
        restarted.extensions["siniestros_repository"].engine.dispose()


def test_upload_quota_rejection_is_fixed_after_quota_is_freed(runtime: dict) -> None:
    from dataclasses import replace

    user = runtime["users"][0]
    status, claim = call(
        runtime["mobile"] + "/siniestros",
        "POST",
        user["claimPayload"],
        user["token"],
        {"Idempotency-Key": str(uuid4())},
    )
    assert status == 201
    app = create_app(replace(runtime["config"], max_count=1))
    client = app.test_client()
    headers = {"Authorization": f"Bearer {runtime['internal']}", "X-Cliente-ID": user["id"]}
    route = f"/v1/siniestros/{claim['id']}/cargas"
    payload = {"mimeType": "image/jpeg", "tamanoBytes": 100, "sha256": "0" * 64}
    try:
        accepted = client.post(
            route, json=payload, headers={**headers, "Idempotency-Key": str(uuid4())}
        )
        assert accepted.status_code == 201
        key = {"Idempotency-Key": str(uuid4())}
        rejected = client.post(route, json=payload, headers={**headers, **key})
        assert rejected.status_code == 409
        assert rejected.get_json()["code"] == "limite_cantidad"
        assert (
            client.delete(route + "/" + accepted.get_json()["id"], headers=headers).status_code
            == 204
        )
        replay = client.post(route, json=payload, headers={**headers, **key})
        assert replay.status_code == 409
        assert replay.get_json()["ultimoResultado"] == rejected.get_json()["ultimoResultado"]
        changed = client.post(
            route, json={**payload, "tamanoBytes": 101}, headers={**headers, **key}
        )
        assert (changed.status_code, changed.get_json()["code"]) == (
            409,
            "idempotencia_conflictiva",
        )
    finally:
        app.extensions["siniestros_repository"].engine.dispose()


def test_phase_rejection_lost_ack_recovers_by_get_without_freezing_retry(runtime: dict) -> None:
    data = image_bytes()
    _, url, initial = start_upload(runtime, data)
    user = runtime["users"][0]
    assert initial["ultimoResultado"]["httpStatus"] == 201
    incomplete = call(url + "/confirmacion", "POST", token=user["token"])
    assert (incomplete[0], incomplete[1]["code"]) == (409, "carga_incompleta")
    assert call(url, token=user["token"])[1]["ultimoResultado"] == incomplete[1]["ultimoResultado"]
    assert put(runtime, url, b"x" * len(data))[0] == 200
    rejected = call(url + "/confirmacion", "POST", token=user["token"])
    assert (rejected[0], rejected[1]["code"]) == (422, "integridad_invalida")
    restarted = create_app(runtime["config"])
    headers = {"Authorization": f"Bearer {runtime['internal']}", "X-Cliente-ID": user["id"]}
    route = f"/v1/siniestros/{user['claim']['id']}/cargas/{initial['id']}"
    try:
        recovered = restarted.test_client().get(route, headers=headers).get_json()
        assert (recovered["estado"], recovered["offset"]) == ("pendiente", 0)
        assert recovered["ultimoResultado"] == rejected[1]["ultimoResultado"]
        uploaded = put(runtime, url, data)
        assert uploaded[0] == 200 and uploaded[1]["ultimoResultado"]["code"] is None
        confirmed = call(url + "/confirmacion", "POST", token=user["token"])
        assert confirmed[0] == 200 and confirmed[1]["estado"] == "confirmada"
        assert confirmed[1]["ultimoResultado"]["httpStatus"] == 200
        for status, problem in (
            put(runtime, url, data),
            call(url, "DELETE", token=user["token"]),
        ):
            assert (status, problem["code"]) == (409, "carga_confirmada")
            assert problem["ultimoResultado"]["httpStatus"] == status
            assert problem["ultimoResultado"]["code"] == problem["code"]
            assert problem["ultimoResultado"]["operacionId"] == initial["ultimoResultado"][
                "operacionId"
            ]
        assert call(url, token=user["token"])[1] == confirmed[1]
        assert call(url + "/confirmacion", "POST", token=user["token"]) == confirmed
    finally:
        restarted.extensions["siniestros_repository"].engine.dispose()


def test_rejected_operation_is_isolated_by_owner_and_type(runtime: dict) -> None:
    first, second = runtime["users"]
    key = {"Idempotency-Key": str(uuid4())}
    root = runtime["mobile"] + "/siniestros"
    rejected = call(
        root,
        "POST",
        {**first["claimPayload"], "fechaEvento": "2099-01-01T00:00:00Z"},
        first["token"],
        key,
    )
    assert rejected[0] == 422
    other = call(root, "POST", second["claimPayload"], second["token"], key)
    assert other[0] == 201
    assert other[1]["ultimoResultado"]["code"] is None
    payload = {"mimeType": "image/jpeg", "tamanoBytes": 100, "sha256": "0" * 64}
    own = root + "/" + first["claim"]["id"] + "/cargas"
    created = call(own, "POST", payload, first["token"], key)
    assert created[0] == 201
    foreign = call(own, "POST", payload, second["token"], key)
    assert (foreign[0], foreign[1]["code"]) == (404, "siniestro_no_encontrado")
    assert "ultimoResultado" not in foreign[1]
    assert call(own + "/" + created[1]["id"], token=second["token"])[0] == 404


def test_concurrent_rejections_share_one_receipt_and_one_binding(runtime: dict) -> None:
    user = runtime["users"][0]
    root = runtime["mobile"] + "/siniestros"
    key = str(uuid4())
    payload = {**user["claimPayload"], "fechaEvento": "2099-01-01T00:00:00Z"}
    with ThreadPoolExecutor(max_workers=4) as executor:
        responses = list(
            executor.map(
                lambda _: call(root, "POST", payload, user["token"], {"Idempotency-Key": key}),
                range(4),
            )
        )
    assert {status for status, _ in responses} == {422}
    assert all(
        body["ultimoResultado"] == responses[0][1]["ultimoResultado"] for _, body in responses
    )
    repo = runtime["app"].extensions["siniestros_repository"]
    with repo.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(Audit)
                .where(Audit.actor == user["id"], Audit.operation == key)
            )
            == 1
        )


def test_integrity_guard_blocks_new_and_pending_writes_but_preserves_receipts(
    runtime: dict,
) -> None:
    user = runtime["users"][0]
    root = runtime["mobile"] + "/siniestros"
    key = {"Idempotency-Key": str(uuid4())}
    status, claim = call(root, "POST", user["claimPayload"], user["token"], key)
    assert status == 201
    data = image_bytes()
    payload = {
        "mimeType": "image/jpeg",
        "tamanoBytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    route = root + "/" + claim["id"] + "/cargas"
    confirmed_key = {"Idempotency-Key": str(uuid4())}
    _, confirmed = call(route, "POST", payload, user["token"], confirmed_key)
    url = route + "/" + confirmed["id"]
    assert put(runtime, url, data)[0] == 200
    receipt = call(url + "/confirmacion", "POST", token=user["token"])[1]
    pending_key = {"Idempotency-Key": str(uuid4())}
    _, pending = call(route, "POST", payload, user["token"], pending_key)
    pending_url = route + "/" + pending["id"]
    repo = runtime["app"].extensions["siniestros_repository"]
    with repo.write() as session:
        session.get(Claim, claim["id"]).estado = "en_evaluacion"
    assert call(root + "/" + claim["id"] + "/estado", token=user["token"])[0] == 503
    for status, body in (
        call(route, "POST", payload, user["token"], {"Idempotency-Key": str(uuid4())}),
        put(runtime, pending_url, data),
        call(pending_url + "/confirmacion", "POST", token=user["token"]),
    ):
        assert (status, body["code"]) == (503, "siniestro_no_integro")
        assert "ultimoResultado" not in body
    assert call(root, "POST", user["claimPayload"], user["token"], key) == (200, claim)
    assert call(route, "POST", payload, user["token"], confirmed_key) == (200, receipt)
    assert call(url + "/confirmacion", "POST", token=user["token"]) == (200, receipt)
    assert call(pending_url, token=user["token"])[1] == pending
    assert not (runtime["config"].storage_root / pending["id"]).exists()


@pytest.mark.parametrize("rejected", [False, True])
@pytest.mark.parametrize("failure", ["audit", "commit"])
def test_audit_failure_never_commits_business_result(
    runtime: dict, rejected: bool, failure: str
) -> None:
    user = runtime["users"][0]
    root = runtime["mobile"] + "/siniestros"
    key = str(uuid4())
    payload = dict(user["claimPayload"])
    if rejected:
        payload["fechaEvento"] = "2099-01-01T00:00:00Z"
    repo = runtime["app"].extensions["siniestros_repository"]

    def fail_audit(
        connection: Connection,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        if statement.startswith("INSERT INTO auditoria_siniestros"):
            raise SQLAlchemyError("synthetic audit write failure")

    def fail_commit(connection: Connection) -> None:
        raise OSError("synthetic commit failure")

    event_name = "commit" if failure == "commit" else "before_cursor_execute"
    listener = fail_commit if failure == "commit" else fail_audit
    event.listen(repo.engine, event_name, listener)
    try:
        status, body = call(root, "POST", payload, user["token"], {"Idempotency-Key": key})
        assert (status, body["code"]) == (503, "servicio_no_disponible")
        assert "ultimoResultado" not in body
    finally:
        event.remove(repo.engine, event_name, listener)
    with repo.sessions() as session:
        assert session.scalar(select(Claim).where(Claim.operation == key)) is None
        assert session.scalar(select(Audit).where(Audit.operation == key)) is None
    assert call(root, "POST", payload, user["token"], {"Idempotency-Key": key})[0] == (
        422 if rejected else 201
    )


@pytest.mark.parametrize("phase", ["append", "confirm"])
def test_phase_audit_failure_does_not_acknowledge_uncommitted_work(
    runtime: dict, phase: str
) -> None:
    data = image_bytes()
    _, url, upload = start_upload(runtime, data)
    user = runtime["users"][0]
    repo = runtime["app"].extensions["siniestros_repository"]
    if phase == "confirm":
        assert put(runtime, url, data)[0] == 200
    before = call(url, token=user["token"])[1]

    def fail_audit(
        connection: Connection,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        if statement.startswith("INSERT INTO auditoria_siniestros"):
            raise SQLAlchemyError("synthetic phase audit failure")

    event.listen(repo.engine, "before_cursor_execute", fail_audit)
    try:
        status, body = (
            put(runtime, url, data)
            if phase == "append"
            else call(url + "/confirmacion", "POST", token=user["token"])
        )
        assert status == 503 and "ultimoResultado" not in body
    finally:
        event.remove(repo.engine, "before_cursor_execute", fail_audit)
    assert call(url, token=user["token"])[1] == before
    if phase == "append":
        assert put(runtime, url, data)[0] == 200
    assert call(url + "/confirmacion", "POST", token=user["token"])[1]["estado"] == "confirmada"
    with repo.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(Audit)
                .where(
                    Audit.operation == upload["ultimoResultado"]["operacionId"],
                    Audit.action == "confirmar_carga",
                    Audit.http_status == 200,
                )
            )
            == 1
        )


def test_concurrent_different_payloads_bind_uuid_to_only_one_result(runtime: dict) -> None:
    user = runtime["users"][0]
    root = runtime["mobile"] + "/siniestros"
    key = str(uuid4())
    payloads = [user["claimPayload"], {**user["claimPayload"], "lugar": "Otro lugar"}]
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(
            executor.map(
                lambda payload: call(
                    root, "POST", payload, user["token"], {"Idempotency-Key": key}
                ),
                payloads,
            )
        )
    assert sorted(status for status, _ in responses) == [201, 409]
    for payload, (status, body) in zip(payloads, responses, strict=True):
        replay = call(root, "POST", payload, user["token"], {"Idempotency-Key": key})
        assert replay[0] == (200 if status == 201 else 409)
        assert replay[1]["ultimoResultado"] == body["ultimoResultado"]
    repo = runtime["app"].extensions["siniestros_repository"]
    with repo.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(Claim)
                .where(Claim.owner == user["id"], Claim.operation == key)
            )
            == 1
        )


def test_dependency_failure_does_not_bind_final_rejection(
    runtime: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = runtime["users"][0]
    root = runtime["mobile"] + "/siniestros"
    key = {"Idempotency-Key": str(uuid4())}

    def unavailable(*args: Any) -> Any:
        raise Problem(503, "servicio_no_disponible")

    with monkeypatch.context() as patch:
        patch.setattr("siniestros.repository.policies", unavailable)
        status, body = call(root, "POST", user["claimPayload"], user["token"], key)
        assert status == 503 and "ultimoResultado" not in body
    assert call(root, "POST", user["claimPayload"], user["token"], key)[0] == 201


def test_authorization_is_checked_before_accepted_replay(runtime: dict) -> None:
    user, other = runtime["users"]
    data = image_bytes()
    root, url, upload = start_upload(runtime, data)
    repo = runtime["app"].extensions["siniestros_repository"]
    with repo.sessions() as session:
        key = session.get(Upload, upload["id"]).operation
    assert put(runtime, url, data)[0] == 200
    assert call(url + "/confirmacion", "POST", token=user["token"])[0] == 200
    with repo.write() as session:
        session.get(Claim, user["claim"]["id"]).owner = other["id"]
    try:
        payload = {
            "mimeType": "image/jpeg",
            "tamanoBytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        status, body = call(
            root + "/cargas", "POST", payload, user["token"], {"Idempotency-Key": key}
        )
        assert (status, body["code"]) == (404, "siniestro_no_encontrado")
        assert "ultimoResultado" not in body
        assert call(url, token=user["token"])[0] == 404
        assert call(url + "/confirmacion", "POST", token=user["token"])[0] == 404
    finally:
        with repo.write() as session:
            session.get(Claim, user["claim"]["id"]).owner = user["id"]
