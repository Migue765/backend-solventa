from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from http.client import IncompleteRead
from threading import Barrier
from uuid import UUID

import pytest
from flask import Flask
from identidad.consent_outbox import MAX_ATTEMPTS, ConsentOutbox
from identidad.domain.consent import Consent, ConsentScope, ConsentVersionConflictError
from identidad.infrastructure.persistence.consent_repository import SqlConsentRepository
from identidad.infrastructure.persistence.models import ConsentEventModel, ConsentModel
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

TOKEN = "token-interno-de-prueba"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def scope() -> dict[str, object]:
    return {
        "finalidad": "cotizacion",
        "alcances": ["open_finance", "open_data"],
        "fuentes": ["open_finance", "open_data"],
        "datos": ["indicador_riesgo"],
        "versionTerminos": "1.0",
        "vigenteHasta": (datetime.now(UTC) + timedelta(days=29)).isoformat(),
    }


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
    consent = client.put(
        path, headers=HEADERS, json={"autorizado": True, "version": 0, "solicitud": scope()}
    )
    assert consent.status_code == 200
    data = consent.get_json()
    assert data["estado"] == "autorizado"
    assert (
        timedelta(days=28)
        < datetime.fromisoformat(data["vigenteHasta"]) - datetime.fromisoformat(data["efectivaEn"])
        <= timedelta(days=30)
    )
    assert data["actorId"] == user
    assert data["versionTerminos"] == "1.0"
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
    assert revoked.get_json()["vigenteHasta"] == data["vigenteHasta"]
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


@pytest.mark.parametrize(
    "missing", ["finalidad", "alcances", "fuentes", "datos", "versionTerminos", "vigenteHasta"]
)
def test_incomplete_decisions_never_persist(
    make_app: Callable[..., Flask], database_url: str, missing: str
) -> None:
    app = make_app(internal_api_token=TOKEN)
    path = f"/v1/clientes/{register(app)}/consentimiento-perfilamiento"
    request_scope = scope()
    request_scope.pop(missing)
    for authorized in (True, False):
        response = app.test_client().put(
            path,
            headers=HEADERS,
            json={"autorizado": authorized, "version": 0, "solicitud": request_scope},
        )
        assert response.status_code == 400
    with Session(create_engine(database_url)) as session:
        assert session.scalar(select(func.count()).select_from(ConsentModel)) == 0
        assert session.scalar(select(func.count()).select_from(ConsentEventModel)) == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"fuentes": []},
        {"alcances": ["open_data"], "fuentes": ["open_finance"]},
        {"fuentes": ["banco_real"]},
        {"datos": ["movimientos_bancarios"]},
        {"versionTerminos": ""},
        {"versionTerminos": "desconocida"},
        {"actorId": "otro"},
        {"vigenteHasta": "2020-01-01T00:00:00Z"},
        {"vigenteHasta": "2099-01-01T00:00:00Z"},
        {"vigenteHasta": "2026-12-01T00:00:00"},
    ],
)
def test_invalid_scope_never_authorizes(
    make_app: Callable[..., Flask], changes: dict[str, object]
) -> None:
    app = make_app(internal_api_token=TOKEN)
    path = f"/v1/clientes/{register(app)}/consentimiento-perfilamiento"
    response = app.test_client().put(
        path,
        headers=HEADERS,
        json={"autorizado": True, "version": 0, "solicitud": {**scope(), **changes}},
    )
    assert response.status_code == 400
    assert app.test_client().get(path, headers=HEADERS).status_code == 404


def test_rejection_and_expiration_preserve_evidence(
    make_app: Callable[..., Flask], database_url: str
) -> None:
    app = make_app(internal_api_token=TOKEN)
    path = f"/v1/clientes/{register(app)}/consentimiento-perfilamiento"
    client = app.test_client()
    assert (
        client.put(path, headers=HEADERS, json={"autorizado": True, "version": 0}).status_code
        == 400
    )
    rejected = client.put(
        path, headers=HEADERS, json={"autorizado": False, "version": 0, "solicitud": scope()}
    )
    assert rejected.get_json()["estado"] == "rechazado"
    approved = client.put(
        path, headers=HEADERS, json={"autorizado": True, "version": 1, "solicitud": scope()}
    )
    assert approved.status_code == 200
    expiry = datetime.fromisoformat(approved.get_json()["vigenteHasta"])
    app.extensions["clock"] = lambda: expiry
    assert client.get(path, headers=HEADERS).get_json()["estado"] == "vencido"
    with Session(create_engine(database_url)) as session:
        events = list(session.scalars(select(ConsentEventModel)))
        assert {row.tipo for row in events} == {
            "ConsentimientoAutorizado",
            "ConsentimientoRechazado",
        }
        assert all(row.contenido["actorId"] for row in events)
        assert session.scalar(select(func.count()).select_from(ConsentModel)) == 2


@pytest.mark.parametrize("revoke", [False, True])
def test_outbox_failure_rolls_back_decision(
    make_app: Callable[..., Flask], database_url: str, revoke: bool
) -> None:
    app = make_app(internal_api_token=TOKEN)
    path = f"/v1/clientes/{register(app)}/consentimiento-perfilamiento"
    payload = {"autorizado": True, "version": 0, "solicitud": scope()}
    if revoke:
        assert app.test_client().put(path, headers=HEADERS, json=payload).status_code == 200
        payload = {"autorizado": False, "version": 1}

    def fail_insert(*args: object) -> None:
        raise OperationalError("outbox", {}, RuntimeError("unavailable"))

    event.listen(ConsentEventModel, "before_insert", fail_insert)
    try:
        response = app.test_client().put(path, headers=HEADERS, json=payload)
        assert response.status_code == 503
    finally:
        event.remove(ConsentEventModel, "before_insert", fail_insert)
    with Session(create_engine(database_url)) as session:
        assert session.scalar(select(func.count()).select_from(ConsentModel)) == int(revoke)
        assert session.scalar(select(func.count()).select_from(ConsentEventModel)) == int(revoke)
    if revoke:
        assert app.test_client().get(path, headers=HEADERS).get_json()["estado"] == "autorizado"


def test_outbox_retries_dead_letter_and_explicit_recovery(
    make_app: Callable[..., Flask], database_url: str
) -> None:
    app = make_app(internal_api_token=TOKEN)
    path = f"/v1/clientes/{register(app)}/consentimiento-perfilamiento"
    assert (
        app.test_client()
        .put(path, headers=HEADERS, json={"autorizado": True, "version": 0, "solicitud": scope()})
        .status_code
        == 200
    )
    sessions = sessionmaker(create_engine(database_url))

    def fail_publish(content: dict[str, object]) -> dict[str, object]:
        raise OSError("down")

    failing = ConsentOutbox(sessions, fail_publish)
    for _ in range(MAX_ATTEMPTS + 1):
        assert failing.deliver() == (0, 1)
    with sessions() as session:
        row = session.scalars(select(ConsentEventModel)).one()
        assert row.intentos == MAX_ATTEMPTS
        assert row.publicado is False
        assert row.ultimo_error == "OSError"
        event_id = row.evento_id

    def confirm(content: dict[str, object]) -> dict[str, object]:
        return {
            "eventoId": content["eventoId"],
            "versionAplicada": content["version"],
            "aplicadaEn": datetime.now(UTC).isoformat(),
        }

    recovered = ConsentOutbox(sessions, confirm)
    assert recovered.deliver(retry_failed=True) == (1, 0)
    assert recovered.deliver() == (0, 0)
    with sessions() as session:
        recovered_row = session.get(ConsentEventModel, event_id)
        assert recovered_row is not None and recovered_row.publicado and recovered_row.acuse
        assert UUID(str(recovered_row.acuse["eventoId"])) == event_id


def test_concurrent_decisions_have_one_winner_and_one_event(
    make_app: Callable[..., Flask], database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(internal_api_token=TOKEN)
    user_id = UUID(register(app))
    repository = SqlConsentRepository(sessionmaker(create_engine(database_url)))
    original_get = repository.get
    barrier = Barrier(2)

    def read_same_version(user: UUID) -> Consent | None:
        current = original_get(user)
        if current is None:
            barrier.wait(timeout=5)
        return current

    monkeypatch.setattr(repository, "get", read_same_version)

    def decide(_: int) -> str:
        try:
            repository.record(
                user_id,
                True,
                0,
                datetime.now(UTC),
                "concurrente",
                ConsentScope.model_validate(scope()),
            )
            return "persistida"
        except ConsentVersionConflictError:
            return "conflicto"

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(decide, range(2))) == ["conflicto", "persistida"]
    with Session(create_engine(database_url)) as session:
        assert session.scalar(select(func.count()).select_from(ConsentModel)) == 1
        assert session.scalar(select(func.count()).select_from(ConsentEventModel)) == 1


@pytest.mark.parametrize(
    "invalid",
    [
        {},
        {"eventoId": "otro"},
        {"versionAplicada": True},
        {"versionAplicada": 0},
        {"aplicadaEn": "no-fecha"},
        {"aplicadaEn": "2026-01-01T00:00:00"},
        {"aplicadaEn": "2000-01-01T00:00:00Z"},
        {"aplicadaEn": "2099-01-01T00:00:00Z"},
    ],
)
def test_invalid_ack_never_marks_event_as_delivered(
    make_app: Callable[..., Flask], database_url: str, invalid: dict[str, object]
) -> None:
    app = make_app(internal_api_token=TOKEN)
    path = f"/v1/clientes/{register(app)}/consentimiento-perfilamiento"
    assert (
        app.test_client()
        .put(path, headers=HEADERS, json={"autorizado": True, "version": 0, "solicitud": scope()})
        .status_code
        == 200
    )
    sessions = sessionmaker(create_engine(database_url))

    def bad_ack(content: dict[str, object]) -> dict[str, object]:
        if not invalid:
            return {}
        return {
            "eventoId": content["eventoId"],
            "versionAplicada": content["version"],
            "aplicadaEn": datetime.now(UTC).isoformat(),
            **invalid,
        }

    assert ConsentOutbox(sessions, bad_ack).deliver() == (0, 1)
    with sessions() as session:
        row = session.scalars(select(ConsentEventModel)).one()
        assert not row.publicado and row.acuse is None and row.intentos == 1
        assert row.ultimo_error == "ValueError"


def test_interrupted_response_is_retried_and_attempt_limit_is_rechecked(
    make_app: Callable[..., Flask], database_url: str
) -> None:
    app = make_app(internal_api_token=TOKEN)
    path = f"/v1/clientes/{register(app)}/consentimiento-perfilamiento"
    assert (
        app.test_client()
        .put(path, headers=HEADERS, json={"autorizado": True, "version": 0, "solicitud": scope()})
        .status_code
        == 200
    )
    sessions = sessionmaker(create_engine(database_url))

    def interrupted(content: dict[str, object]) -> dict[str, object]:
        raise IncompleteRead(b"")

    worker = ConsentOutbox(sessions, interrupted)
    assert worker.deliver() == (0, 1)
    with sessions.begin() as session:
        row = session.scalars(select(ConsentEventModel)).one()
        assert row.ultimo_error == "IncompleteRead"
        event_id = row.evento_id
        row.intentos = MAX_ATTEMPTS
    assert worker._deliver_one(event_id) is None
    with sessions() as session:
        exhausted = session.get(ConsentEventModel, event_id)
        assert exhausted is not None and exhausted.intentos == MAX_ATTEMPTS


def test_partial_scope_is_preserved_without_implicit_permissions(
    make_app: Callable[..., Flask],
) -> None:
    app = make_app(internal_api_token=TOKEN)
    path = f"/v1/clientes/{register(app)}/consentimiento-perfilamiento"
    partial = {**scope(), "alcances": ["open_data"], "fuentes": ["open_data"]}
    response = app.test_client().put(
        path, headers=HEADERS, json={"autorizado": True, "version": 0, "solicitud": partial}
    )
    assert response.status_code == 200
    assert response.get_json()["alcances"] == ["open_data"]
    assert response.get_json()["fuentes"] == ["open_data"]
