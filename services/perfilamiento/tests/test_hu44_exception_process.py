from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from typing import Literal
from uuid import UUID, uuid4

import pytest
from flask import Flask
from perfilamiento.adapters import FuenteMock
from perfilamiento.app import Config, create_app
from perfilamiento.domain import Consentimiento, ErrorPerfilamiento, Fuente
from perfilamiento.exceptions import ExcepcionOpenFinance
from perfilamiento.persistence import (
    Base,
    ConsultaInformacionModel,
    EventoExcepcionOpenFinanceModel,
    ExcepcionOpenFinanceModel,
    FuenteCacheModel,
    OperacionInformacionModel,
)
from sqlalchemy import Connection, event, func, select, update
from sqlalchemy.orm import Mapper, Session
from werkzeug.test import TestResponse

TOKEN = "Bearer hu44-process-test"


class Consents:
    def __init__(self) -> None:
        now = datetime.now(UTC)
        client = uuid4()
        self.current = Consentimiento(
            consentimiento_id=uuid4(),
            cliente_id=client,
            version=1,
            estado="autorizado",
            finalidad="cotizacion",
            alcances=("open_finance", "open_data"),
            fuentes=("open_finance", "open_data"),
            datos=("indicador_riesgo",),
            actor_id=client,
            version_terminos="1.0",
            efectiva_en=now - timedelta(hours=1),
            vigente_hasta=now + timedelta(hours=1),
        )

    def obtener(self, cliente_id: UUID, correlation_id: str) -> Consentimiento:
        assert cliente_id == self.current.cliente_id
        return self.current


class Authorizations:
    def __init__(self) -> None:
        self.requester = uuid4()
        self.advisor = uuid4()
        self.allowed = {(self.requester, "socio"), (self.advisor, "asesor")}

    def validar(
        self, actor_id: UUID, socio_id: str, rol: Literal["asesor", "socio"], correlation_id: str
    ) -> None:
        if socio_id != "BANCO-HU44" or (actor_id, rol) not in self.allowed:
            raise ErrorPerfilamiento("actor_no_autorizado", 403)


class Source:
    def __init__(self, nombre: Literal["open_finance", "open_data"], available: bool) -> None:
        self.nombre = nombre
        self.available = available
        self.calls = 0
        self.on_call: Callable[[], None] | None = None

    def consultar(self, cliente_id: UUID, ahora: datetime) -> Fuente:
        self.calls += 1
        if self.on_call is not None:
            self.on_call()
        return FuenteMock(
            self.nombre, "disponible" if self.available else "no_disponible"
        ).consultar(cliente_id, ahora)


@pytest.fixture
def process_database_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'hu44.db'}"


@pytest.fixture
def process_app(process_database_url: str) -> Iterator[tuple[Flask, Consents]]:
    consents = Consents()
    authorizations = Authorizations()
    sources = (Source("open_finance", False), Source("open_data", True))
    app = create_app(
        Config(
            internal_api_token="hu44-process-test",
            database_url=process_database_url,
        ),
        consentimientos=consents,
        fuentes=sources,
        autorizaciones=authorizations,
    )
    app.extensions["test_authorizations"] = authorizations
    app.extensions["test_sources"] = sources
    app.extensions["perfiles"].inicializar()
    try:
        yield app, consents
    finally:
        app.extensions["cerrar_recursos_perfilamiento"]()


def test_real_hu44_block_creates_linked_case(process_app: tuple[Flask, Consents]) -> None:
    app, consents = process_app
    client = app.test_client()
    operation = str(uuid4())
    response = client.post(
        "/v1/consultas-informacion-cliente",
        headers={"Authorization": TOKEN},
        json={
            "operationId": operation,
            "socioId": "BANCO-HU44",
            "actorId": str(uuid4()),
            "clienteId": str(consents.current.cliente_id),
            "productoId": "mortgage-life",
            "finalidad": "cotizacion",
            "datosSolicitados": ["indicador_riesgo"],
        },
    )
    assert response.status_code == 503
    cases = client.get(
        "/v1/excepciones-open-finance?socioId=BANCO-HU44", headers={"Authorization": TOKEN}
    ).get_json()["excepciones"]
    assert len(cases) == 1
    assert cases[0]["operationId"] == operation
    assert cases[0]["proceso"]["tipo"] == "consulta_informacion_cliente"
    assert cases[0]["aplicacion"]["estado"] == "esperando_decision"
    assert cases[0]["aplicacion"]["acuseId"] is None
    assert all(cases[0][field] is None for field in ("decision", "actorId", "resueltaEn", "motivo"))


def test_historical_boolean_does_not_authorize_application(
    process_app: tuple[Flask, Consents],
) -> None:
    app, consents = process_app
    client = app.test_client()
    opened = client.post(
        "/v1/excepciones-open-finance",
        headers={"Authorization": TOKEN},
        json={
            "operationId": str(uuid4()),
            "socioId": "BANCO-HU44",
            "clienteId": str(consents.current.cliente_id),
            "consentimientoId": str(consents.current.consentimiento_id),
            "consentimientoVersion": 1,
            "codigo": "regla_no_aplicable",
            "motivoBloqueo": "Caso historico sin operacion demostrable",
            "bloqueaProcesamiento": True,
        },
    )
    assert opened.status_code == 201
    exception_id = opened.get_json()["excepcionId"]
    repository = app.extensions["perfiles"]
    with Session(repository.engine) as session:
        historical = session.get(ExcepcionOpenFinanceModel, UUID(exception_id))
        assert historical is not None
        before = historical.contenido, historical.hash_contenido
    Base.metadata.tables["operaciones_informacion"].drop(repository.engine)
    repository.inicializar()
    with Session(repository.engine) as session:
        historical = session.get(ExcepcionOpenFinanceModel, UUID(exception_id))
        assert historical is not None
        assert (historical.contenido, historical.hash_contenido) == before
    assert _count(app, OperacionInformacionModel) == 0
    assert _count(app, EventoExcepcionOpenFinanceModel) == 1
    detail = client.get(
        f"/v1/excepciones-open-finance/{exception_id}?socioId=BANCO-HU44",
        headers={"Authorization": TOKEN},
    ).get_json()
    assert detail["proceso"] is None and detail["aplicacion"] is None
    result = client.post(
        f"/v1/excepciones-open-finance/{exception_id}/aplicar?socioId=BANCO-HU44",
        headers={"Authorization": TOKEN, "X-Actor-ID": str(uuid4())},
        json={},
    )
    assert result.status_code == 409
    assert result.get_json()["error"] == "excepcion_sin_operacion"


def _block(app: Flask, consents: Consents) -> tuple[dict[str, object], ExcepcionOpenFinance]:
    command: dict[str, object] = {
        "operationId": str(uuid4()),
        "socioId": "BANCO-HU44",
        "actorId": str(app.extensions["test_authorizations"].requester),
        "clienteId": str(consents.current.cliente_id),
        "productoId": "mortgage-life",
        "finalidad": "cotizacion",
        "datosSolicitados": ["indicador_riesgo", "dato_no_autorizado"],
    }
    client = app.test_client()
    response = client.post(
        "/v1/consultas-informacion-cliente", json=command, headers={"Authorization": TOKEN}
    )
    assert response.status_code == 503
    state = response.get_json()["operacion"]
    opened = client.get(
        f"/v1/excepciones-open-finance/{state['excepcionId']}?socioId=BANCO-HU44",
        headers={"Authorization": TOKEN},
    ).get_json()
    return command, ExcepcionOpenFinance.model_validate(opened)


def _decide(app: Flask, case: ExcepcionOpenFinance, decision: str = "aprobar") -> TestResponse:
    return app.test_client().post(
        f"/v1/excepciones-open-finance/{case.excepcion_id}/resolver?socioId=BANCO-HU44",
        json={"decision": decision, "motivo": "Revision de la operacion bloqueada"},
        headers={
            "Authorization": TOKEN,
            "X-Actor-ID": str(app.extensions["test_authorizations"].advisor),
        },
    )


def _apply(app: Flask, case: ExcepcionOpenFinance, **payload: object) -> TestResponse:
    return app.test_client().post(
        f"/v1/excepciones-open-finance/{case.excepcion_id}/aplicar?socioId=BANCO-HU44",
        json=payload,
        headers={
            "Authorization": TOKEN,
            "X-Actor-ID": str(app.extensions["test_authorizations"].advisor),
        },
    )


def _count(app: Flask, model: type[Base]) -> int:
    with Session(app.extensions["perfiles"].engine) as session:
        value = session.scalar(select(func.count()).select_from(model))
        assert isinstance(value, int)
        return value


def _clone(app: Flask, consents: Consents) -> Flask:
    cloned = create_app(
        Config(
            internal_api_token="hu44-process-test",
            database_url=str(app.extensions["perfiles"].engine.url),
        ),
        consentimientos=consents,
        fuentes=app.extensions["test_sources"],
        autorizaciones=app.extensions["test_authorizations"],
    )
    cloned.extensions["test_authorizations"] = app.extensions["test_authorizations"]
    return cloned


def test_decision_is_distinct_from_atomic_result_and_requester_recovery(
    process_app: tuple[Flask, Consents],
) -> None:
    app, consents = process_app
    command, case = _block(app, consents)
    assert case.proceso is not None
    assert case.proceso.datos_solicitados == ("indicador_riesgo",)
    assert _apply(app, case).get_json()["error"] == "decision_pendiente"
    decided = _decide(app, case)
    assert decided.status_code == 200
    assert decided.get_json()["estado"] == "aprobada"
    assert decided.get_json()["aplicacion"]["estado"] == "pendiente"
    assert _count(app, ConsultaInformacionModel) == 0
    pending = (
        app.test_client()
        .get(
            "/v1/excepciones-open-finance?socioId=BANCO-HU44&estadoAplicacion=pendiente",
            headers={"Authorization": TOKEN},
        )
        .get_json()["excepciones"]
    )
    assert [row["excepcionId"] for row in pending] == [str(case.excepcion_id)]
    app.extensions["test_sources"][0].available = True
    result = _apply(app, case)
    assert result.status_code == 200
    application = result.get_json()["aplicacion"]
    assert application["estado"] == "completada"
    assert application["intentos"] == 1
    assert application["acuseId"] is not None
    assert _apply(app, case).get_json()["aplicacion"] == application
    assert _decide(app, case).get_json()["aplicacion"] == application
    assert _count(app, ConsultaInformacionModel) == 1
    assert _count(app, EventoExcepcionOpenFinanceModel) == 3
    assert app.extensions["test_sources"][0].calls == 3
    state = app.test_client().get(
        f"/v1/consultas-informacion-cliente/{command['operationId']}/estado?socioId=BANCO-HU44",
        headers={"Authorization": TOKEN, "X-Actor-ID": str(command["actorId"])},
    )
    assert state.status_code == 200
    assert state.get_json()["aplicacion"] == application
    recovered = app.test_client().post(
        "/v1/consultas-informacion-cliente", json=command, headers={"Authorization": TOKEN}
    )
    assert recovered.status_code == 201
    assert recovered.get_json()["operationId"] == command["operationId"]
    assert recovered.get_json()["estado"] == "completada"
    assert _count(app, ConsultaInformacionModel) == 1


@pytest.mark.parametrize("failure", ["source", "consent", "requester", "during", "catalog"])
def test_failed_retry_is_blocked_and_cannot_loop(
    process_app: tuple[Flask, Consents], failure: str
) -> None:
    app, consents = process_app
    command, case = _block(app, consents)
    assert _decide(app, case).status_code == 200
    source = app.extensions["test_sources"][0]
    source.available = failure != "source"
    if failure == "consent":
        consents.current = consents.current.model_copy(update={"estado": "revocado"})
    elif failure == "requester":
        app.extensions["test_authorizations"].allowed.remove(
            (UUID(str(command["actorId"])), "socio")
        )
    elif failure == "during":
        source.on_call = lambda: setattr(
            consents, "current", consents.current.model_copy(update={"estado": "revocado"})
        )
    result = _apply(
        app, case, **({"impedimento": "producto_no_autorizado"} if failure == "catalog" else {})
    )
    assert result.status_code == 200
    application = result.get_json()["aplicacion"]
    assert application["estado"] == "bloqueada"
    assert application["intentos"] == 1
    assert application["acuseId"] is not None
    calls = source.calls
    assert _apply(app, case).get_json()["aplicacion"] == application
    assert source.calls == calls
    assert _count(app, ConsultaInformacionModel) == 0


def test_rejection_stops_the_operation_without_reading_sources(
    process_app: tuple[Flask, Consents],
) -> None:
    app, consents = process_app
    command, case = _block(app, consents)
    before = app.extensions["test_sources"][0].calls
    assert _decide(app, case, "rechazar").status_code == 200
    application = _apply(app, case).get_json()["aplicacion"]
    assert application["estado"] == "detenida"
    assert application["intentos"] == 0
    assert application["acuseId"] is not None
    replay = app.test_client().post(
        "/v1/consultas-informacion-cliente", json=command, headers={"Authorization": TOKEN}
    )
    assert replay.status_code == 409
    assert replay.get_json()["error"] == "operacion_rechazada"
    assert app.extensions["test_sources"][0].calls == before
    assert _count(app, ConsultaInformacionModel) == 0
    app.extensions["test_sources"][0].available = True
    other = {**command, "operationId": str(uuid4())}
    assert (
        app.test_client()
        .post("/v1/consultas-informacion-cliente", json=other, headers={"Authorization": TOKEN})
        .status_code
        == 201
    )
    assert _count(app, ConsultaInformacionModel) == 1


def test_two_instances_apply_once_and_share_one_ack(process_app: tuple[Flask, Consents]) -> None:
    app, consents = process_app
    _, case = _block(app, consents)
    assert _decide(app, case).status_code == 200
    app.extensions["test_sources"][0].available = True
    clone = _clone(app, consents)
    barrier = Barrier(2)

    def run(instance: Flask) -> TestResponse:
        barrier.wait(timeout=5)
        return _apply(instance, case)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(run, [app, clone]))
        assert [response.status_code for response in responses] == [200, 200]
        assert responses[0].get_json()["aplicacion"] == responses[1].get_json()["aplicacion"]
        assert _count(app, ConsultaInformacionModel) == 1
        assert _count(app, EventoExcepcionOpenFinanceModel) == 3
        assert app.extensions["test_sources"][0].calls == 3
    finally:
        clone.extensions["cerrar_recursos_perfilamiento"]()


def test_ack_failure_rolls_back_result_and_restart_can_redeliver(
    process_app: tuple[Flask, Consents],
) -> None:
    app, consents = process_app
    _, case = _block(app, consents)
    assert _decide(app, case).status_code == 200
    app.extensions["test_sources"][0].available = True

    def fail_ack(
        mapper: Mapper[EventoExcepcionOpenFinanceModel],
        connection: Connection,
        target: EventoExcepcionOpenFinanceModel,
    ) -> None:
        if target.contenido["tipo"] == "AplicacionExcepcionOpenFinanceRegistrada":
            raise RuntimeError("ack_failure")

    event.listen(EventoExcepcionOpenFinanceModel, "before_insert", fail_ack)
    try:
        assert _apply(app, case).status_code == 503
    finally:
        event.remove(EventoExcepcionOpenFinanceModel, "before_insert", fail_ack)
    assert _count(app, ConsultaInformacionModel) == 0
    assert _count(app, EventoExcepcionOpenFinanceModel) == 2
    clone = _clone(app, consents)
    try:
        result = _apply(clone, case)
        assert result.status_code == 200
        assert result.get_json()["aplicacion"]["estado"] == "completada"
        assert _count(app, ConsultaInformacionModel) == 1
        assert _count(app, EventoExcepcionOpenFinanceModel) == 3
    finally:
        clone.extensions["cerrar_recursos_perfilamiento"]()


@pytest.mark.parametrize("scenario", ["normal", "open_data", "consent", "cache"])
def test_non_blocking_or_unrelated_paths_do_not_create_cases(
    process_app: tuple[Flask, Consents], scenario: str
) -> None:
    app, consents = process_app
    sources = app.extensions["test_sources"]
    sources[0].available = scenario != "cache"
    sources[1].available = scenario != "open_data"
    if scenario == "consent":
        consents.current = consents.current.model_copy(update={"estado": "revocado"})
    elif scenario == "cache":
        source = FuenteMock("open_finance").consultar(
            consents.current.cliente_id, datetime.now(UTC)
        )
        app.extensions["perfiles"].auditar(
            {"resultado": "cache_fixture"}, consents.current, (source,)
        )
    response = app.test_client().post(
        "/v1/consultas-informacion-cliente",
        headers={"Authorization": TOKEN},
        json={
            "operationId": str(uuid4()),
            "socioId": "BANCO-HU44",
            "actorId": str(app.extensions["test_authorizations"].requester),
            "clienteId": str(consents.current.cliente_id),
            "productoId": "mortgage-life",
            "finalidad": "cotizacion",
            "datosSolicitados": ["indicador_riesgo"],
        },
    )
    assert (
        response.status_code
        == {"normal": 201, "open_data": 503, "consent": 403, "cache": 201}[scenario]
    )
    assert _count(app, ExcepcionOpenFinanceModel) == 0
    with Session(app.extensions["perfiles"].engine) as session:
        rows = session.scalars(select(OperacionInformacionModel)).all()
        assert len(rows) == (1 if scenario in {"normal", "cache"} else 0)
        assert all(row.excepcion_id is None and row.contenido is None for row in rows)


def test_blocked_replay_is_idempotent_and_conflicting_body_is_rejected(
    process_app: tuple[Flask, Consents],
) -> None:
    app, consents = process_app
    command, case = _block(app, consents)
    calls = app.extensions["test_sources"][0].calls
    client = app.test_client()
    repeated = client.post(
        "/v1/consultas-informacion-cliente", json=command, headers={"Authorization": TOKEN}
    )
    assert repeated.status_code == 503
    assert repeated.get_json()["operacion"]["excepcionId"] == str(case.excepcion_id)
    conflict = client.post(
        "/v1/consultas-informacion-cliente",
        json={**command, "productoId": "automotive"},
        headers={"Authorization": TOKEN},
    )
    assert conflict.status_code == 409
    assert app.extensions["test_sources"][0].calls == calls
    assert _count(app, ExcepcionOpenFinanceModel) == 1
    assert _count(app, EventoExcepcionOpenFinanceModel) == 1


@pytest.mark.parametrize("cache", ["expired", "corrupt"])
def test_unusable_finance_cache_produces_a_verified_block(
    process_app: tuple[Flask, Consents],
    cache: str,
) -> None:
    app, consents = process_app
    repository = app.extensions["perfiles"]
    queried_at = datetime.now(UTC) - (timedelta(minutes=20) if cache == "expired" else timedelta())
    source = FuenteMock("open_finance").consultar(consents.current.cliente_id, queried_at)
    repository.auditar({"resultado": "cache_fixture"}, consents.current, (source,))
    if cache == "corrupt":
        with Session(repository.engine) as session, session.begin():
            session.execute(update(FuenteCacheModel).values(hash_contenido="0" * 64))
    _, case = _block(app, consents)
    assert case.proceso is not None
    assert case.aplicacion is not None and case.aplicacion.estado == "esperando_decision"
    assert _count(app, ConsultaInformacionModel) == 0
    assert _count(app, ExcepcionOpenFinanceModel) == 1


def test_requester_state_revalidates_current_permissions_and_consent(
    process_app: tuple[Flask, Consents],
) -> None:
    app, consents = process_app
    command, _ = _block(app, consents)
    client = app.test_client()
    path = f"/v1/consultas-informacion-cliente/{command['operationId']}/estado?socioId=BANCO-HU44"
    headers = {"Authorization": TOKEN, "X-Actor-ID": str(command["actorId"])}
    assert client.get(path, headers=headers).status_code == 200
    grants = app.extensions["test_authorizations"].allowed
    grant = (UUID(str(command["actorId"])), "socio")
    grants.remove(grant)
    assert client.get(path, headers=headers).status_code == 403
    grants.add(grant)
    consents.current = consents.current.model_copy(update={"estado": "revocado"})
    assert client.get(path, headers=headers).status_code == 403
    assert _count(app, ConsultaInformacionModel) == 0


def test_unauthorized_application_does_not_consume_a_pending_decision(
    process_app: tuple[Flask, Consents],
) -> None:
    app, consents = process_app
    _, case = _block(app, consents)
    assert _decide(app, case).status_code == 200
    authorization = app.extensions["test_authorizations"]
    authorization.allowed.remove((authorization.advisor, "asesor"))
    assert _apply(app, case).status_code == 403
    state = app.extensions["perfiles"].buscar_operacion_informacion("BANCO-HU44", case.operation_id)
    assert state.aplicacion.estado == "pendiente"
    assert state.aplicacion.acuse_id is None
    assert _count(app, ConsultaInformacionModel) == 0


def test_concurrent_block_prevents_a_late_normal_result(
    process_app: tuple[Flask, Consents],
) -> None:
    app, consents = process_app
    source = app.extensions["test_sources"][0]
    source.available = True
    command = {
        "operationId": str(uuid4()),
        "socioId": "BANCO-HU44",
        "actorId": str(app.extensions["test_authorizations"].requester),
        "clienteId": str(consents.current.cliente_id),
        "productoId": "mortgage-life",
        "finalidad": "cotizacion",
        "datosSolicitados": ["indicador_riesgo"],
    }

    def block_concurrently() -> None:
        source.on_call = None
        source.available = False
        result = app.test_client().post(
            "/v1/consultas-informacion-cliente", json=command, headers={"Authorization": TOKEN}
        )
        assert result.status_code == 503
        assert result.get_json()["operacion"]["aplicacion"]["estado"] == "esperando_decision"
        source.available = True

    source.on_call = block_concurrently
    response = app.test_client().post(
        "/v1/consultas-informacion-cliente", json=command, headers={"Authorization": TOKEN}
    )
    assert response.status_code == 503
    assert response.get_json()["error"] == "consulta_bloqueada"
    assert _count(app, ConsultaInformacionModel) == 0
    assert _count(app, ExcepcionOpenFinanceModel) == 1
