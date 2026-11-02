from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from alembic import command
from cotizacion.app import create_app
from cotizacion.config import Config
from cotizacion.infrastructure.conciliacion_emision import ConciliadorEmision
from cotizacion.infrastructure.pago_simulado import SimuladorPago
from cotizacion.infrastructure.persistence.database import crear_sesiones
from cotizacion.infrastructure.persistence.models import (
    ConciliacionEmisionModel,
    IntentoPagoSimuladoModel,
    OutboxEventoModel,
    PagoSimuladoModel,
    SagaEmisionModel,
)
from cotizacion.infrastructure.persistence.repositorio_pagos import RepositorioPagosSqlAlchemy
from cotizacion.infrastructure.polizas_http import PolizasHttp
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import SQLAlchemyError

from .test_pagos_simulados_api import preparar


def pendiente(client, *, habilitada=True, resultado="confirmado"):
    from uuid import UUID

    base, query, _, _ = preparar(client)
    factory = client.application.extensions["repositorio_pagos"]._sesiones
    repository = RepositorioPagosSqlAlchemy(
        factory,
        emitir_polizas=True,
        conciliar_emision=habilitada,
        proveedor=SimuladorPago(resultado),
    )
    key = uuid4()
    now = datetime.now(UTC)
    payment = repository.confirmar_pago_simulado(
        base.rsplit("/", 1)[1], UUID(query["clienteId"]), key, now
    )
    return repository, payment, base, query, key, now


def test_solo_confirmacion_nueva_opt_in_crea_elegibilidad(client):
    from uuid import UUID

    historical, old, base, query, key, now = pendiente(client, habilitada=False)
    enabled = RepositorioPagosSqlAlchemy(
        historical._sesiones, emitir_polizas=True, conciliar_emision=True
    )
    assert (
        enabled.confirmar_pago_simulado(old.solicitud_id, UUID(query["clienteId"]), key, now) == old
    )
    _, rejection, _, _, _, _ = pendiente(client, resultado="rechazado")
    _, payment, _, _, _, created = pendiente(client)
    with historical._sesiones() as session:
        jobs = session.scalars(select(ConciliacionEmisionModel)).all()
        assert len(jobs) == 1 and jobs[0].pago_id == payment.pago_id
        stored = jobs[0].autorizada_en
        assert (stored.replace(tzinfo=UTC) if stored.tzinfo is None else stored) == created
        assert session.get(PagoSimuladoModel, old.pago_id).emision_solicitada is True
        assert session.get(PagoSimuladoModel, rejection.pago_id) is None
    assert historical.reservar_emision(created + timedelta(seconds=3)) is None
    assert client.get(base + "/pago-simulado", query_string=query).get_json()["id"] == str(
        old.pago_id
    )


@pytest.mark.parametrize("model", [ConciliacionEmisionModel, OutboxEventoModel])
def test_elegibilidad_y_pago_se_revierten_juntos(client, model):
    from uuid import UUID

    base, query, _, subscription = preparar(client)
    factory = client.application.extensions["repositorio_pagos"]._sesiones
    repository = RepositorioPagosSqlAlchemy(factory, emitir_polizas=True, conciliar_emision=True)

    def fail(*_):
        raise RuntimeError("fallo transaccional sintetico")

    event.listen(model, "before_insert", fail)
    try:
        with pytest.raises(RuntimeError, match="transaccional"):
            repository.confirmar_pago_simulado(
                base.rsplit("/", 1)[1], UUID(query["clienteId"]), uuid4(), datetime.now(UTC)
            )
    finally:
        event.remove(model, "before_insert", fail)
    with factory() as session:
        for table in (PagoSimuladoModel, IntentoPagoSimuladoModel, ConciliacionEmisionModel):
            assert session.scalar(select(func.count()).select_from(table)) == 0
        saga = session.get(SagaEmisionModel, UUID(subscription["id"]))
        assert (saga.estado, saga.version) == ("pago_pendiente", 1)


def test_dos_replicas_no_reservan_el_mismo_trabajo(client, database_url):
    _, payment, _, _, _, now = pendiente(client)
    factories = [crear_sesiones(database_url) for _ in range(2)]
    gate = Barrier(2)

    def reserve(factory):
        with factory() as session:
            pid = (
                session.scalar(text("SELECT pg_backend_pid()"))
                if session.bind.dialect.name == "postgresql"
                else None
            )
            gate.wait(timeout=10)
        repository = RepositorioPagosSqlAlchemy(
            factory, emitir_polizas=True, conciliar_emision=True
        )
        return pid, repository.reservar_emision(now + timedelta(seconds=3))

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(reserve, factories))
        claimed = [job for _, job in results if job is not None]
        assert len(claimed) == 1
        assert claimed[0].pago.pago_id == payment.pago_id
        assert claimed[0].intentos == 1
        pids = [pid for pid, _ in results if pid is not None]
        if pids:
            assert len(set(pids)) == 2
    finally:
        for factory in factories:
            factory.kw["bind"].dispose()


def test_lease_expirada_se_recupera_y_reserva_obsoleta_no_revierte_el_exito(client):
    repository, payment, _, _, _, now = pendiente(client)
    first = repository.reservar_emision(now + timedelta(seconds=3))
    assert first is not None
    assert repository.reservar_emision(now + timedelta(seconds=4)) is None
    second = repository.reservar_emision(now + timedelta(seconds=34))
    assert second is not None and second.token != first.token and second.intentos == 2
    assert not repository.aplazar_emision(first, now, "acuse_perdido")
    repository.marcar_emitida(payment, uuid4())
    assert not repository.aplazar_emision(second, now, "acuse_perdido")
    with repository._sesiones() as session:
        job = session.get(ConciliacionEmisionModel, payment.pago_id)
        assert job.estado == "completada" and job.lease_token is None


@pytest.mark.parametrize("crash_last_attempt", [False, True])
def test_backoff_y_limite_durables_no_convierten_pago_en_rechazo(client, crash_last_attempt):
    repository, payment, base, query, _, now = pendiente(client)
    due = now + timedelta(seconds=3)
    for attempt in range(1, 9):
        reservation = repository.reservar_emision(due)
        assert reservation is not None and reservation.intentos == attempt
        if attempt == 8 and crash_last_attempt:
            due += timedelta(seconds=31)
            break
        assert repository.aplazar_emision(reservation, due, "polizas_no_disponible")
        wait = min(2**attempt, 30)
        assert repository.reservar_emision(due + timedelta(seconds=wait - 1)) is None
        due += timedelta(seconds=wait)
    assert repository.reservar_emision(due + timedelta(days=1)) is None
    with repository._sesiones() as session:
        job = session.get(ConciliacionEmisionModel, payment.pago_id)
        assert (job.estado, job.intentos) == ("agotada", 8)
        assert job.ultimo_error in {"polizas_no_disponible", "limite_reservas"}
        assert session.get(SagaEmisionModel, payment.suscripcion_id).estado == "pago_confirmado"
    assert client.get(base + "/pago-simulado", query_string=query).get_json()["estado"] == (
        "confirmado"
    )
    repository.marcar_emitida(payment, uuid4())
    with repository._sesiones() as session:
        assert session.get(ConciliacionEmisionModel, payment.pago_id).estado == "completada"


def test_fallo_outbox_emision_no_completa_trabajo_ni_saga(client):
    repository, payment, _, _, _, now = pendiente(client)
    reservation = repository.reservar_emision(now + timedelta(seconds=3))
    assert reservation is not None

    def fail(*_):
        raise RuntimeError("acuse SQL perdido")

    event.listen(OutboxEventoModel, "before_insert", fail)
    try:
        with pytest.raises(RuntimeError, match="SQL"):
            repository.marcar_emitida(payment, uuid4())
    finally:
        event.remove(OutboxEventoModel, "before_insert", fail)
    with repository._sesiones() as session:
        assert session.get(ConciliacionEmisionModel, payment.pago_id).estado == "en_proceso"
        saga = session.get(SagaEmisionModel, payment.suscripcion_id)
        assert (saga.estado, saga.version) == ("pago_confirmado", 2)
        assert (
            session.scalar(
                select(func.count())
                .select_from(OutboxEventoModel)
                .where(OutboxEventoModel.tipo == "PolizaAcademicaEmitida")
            )
            == 0
        )


def test_migracion_no_activa_historicos_y_rollback_conserva_pago(client, alembic_config):
    repository, payment, base, query, _, _ = pendiente(client, habilitada=False)
    snapshot = client.get(base + "/pago-simulado", query_string=query).get_json()
    command.downgrade(alembic_config, "intentos_pago_simulado")
    command.upgrade(alembic_config, "head")
    with repository._sesiones() as session:
        assert session.scalar(select(func.count()).select_from(ConciliacionEmisionModel)) == 0
        assert session.get(PagoSimuladoModel, payment.pago_id).emision_solicitada is True
    assert client.get(base + "/pago-simulado", query_string=query).get_json() == snapshot


def test_downgrade_rechaza_borrar_elegibilidad_pendiente(client, alembic_config):
    repository, payment, base, query, _, _ = pendiente(client)
    snapshot = client.get(base + "/pago-simulado", query_string=query).get_json()
    with pytest.raises(RuntimeError, match="elegibilidad pendiente"):
        command.downgrade(alembic_config, "intentos_pago_simulado")
    with repository._sesiones() as session:
        assert session.get(ConciliacionEmisionModel, payment.pago_id) is not None
        assert session.scalar(text("SELECT version_num FROM alembic_version")) == (
            "conciliacion_emision"
        )
    repository.marcar_emitida(payment, uuid4())
    command.downgrade(alembic_config, "intentos_pago_simulado")
    assert client.get(base + "/pago-simulado", query_string=query).get_json() == snapshot
    command.upgrade(alembic_config, "head")


def test_activacion_sin_migracion_falla_y_no_arranca_worker(alembic_config):
    command.upgrade(alembic_config, "intentos_pago_simulado")
    with pytest.raises(SQLAlchemyError):
        create_app(
            Config(
                database_url=alembic_config.get_main_option("sqlalchemy.url"),
                internal_api_token="prueba-local",
                polizas_url="http://127.0.0.1:1",
                conciliacion_emision_habilitada=True,
            )
        )


def test_fallo_worker_queda_persistido_y_registrado(client, database_url, caplog):
    repository, payment, _, _, _, now = pendiente(client)
    with repository._sesiones.begin() as session:
        session.get(ConciliacionEmisionModel, payment.pago_id).proximo_intento_en = now
    worker = ConciliadorEmision(database_url, PolizasHttp("http://127.0.0.1:1", "credencial-local"))
    try:
        assert not worker.conciliar_uno()
        with repository._sesiones() as session:
            job = session.get(ConciliacionEmisionModel, payment.pago_id)
            assert (job.estado, job.intentos, job.ultimo_error) == (
                "pendiente",
                1,
                "polizas_no_disponible",
            )
        assert "conciliacion_emision.fallo" in caplog.text
        assert "credencial-local" not in caplog.text
    finally:
        worker.cerrar()
    assert not worker.activo and worker._engine.pool.checkedout() == 0


def test_ciclo_de_vida_del_servicio_cierra_hilo_y_pool(database_url):
    app = create_app(
        Config(
            database_url=database_url,
            polizas_url="http://127.0.0.1:1",
            internal_api_token="prueba-local",
            conciliacion_emision_habilitada=True,
        )
    )
    worker = app.extensions["conciliador_emision"]
    try:
        assert worker.activo
        assert app.test_client().get("/ping").status_code == 200
    finally:
        worker.cerrar()
        app.extensions["repositorio_pagos"]._sesiones.kw["bind"].dispose()
    assert not worker.activo and worker._engine.pool.checkedout() == 0
    assert app.test_client().get("/ping").status_code == 503
    worker.cerrar()
    with pytest.raises(RuntimeError, match="unico ciclo"):
        worker.iniciar()


def test_salida_del_proceso_invoca_cierre_registrado(database_url):
    import os
    import subprocess
    import sys

    script = """
import atexit, json, os
import tests.jmeter.local_stack
from cotizacion.app import create_app
from cotizacion.config import Config
worker = None
def verify_stopped():
    assert worker is not None and not worker.activo
    assert worker._engine.pool.checkedout() == 0
    print(json.dumps({"worker_stopped": True, "checked_out": 0}))
atexit.register(verify_stopped)
app = create_app(Config(database_url=os.environ["OWNED_DB_URL"],
    polizas_url="http://127.0.0.1:1", internal_api_token="prueba-local",
    conciliacion_emision_habilitada=True))
worker = app.extensions["conciliador_emision"]
assert worker.activo
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={**os.environ, "OWNED_DB_URL": database_url, "PYTHONPATH": os.pathsep.join(sys.path)},
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert '"worker_stopped": true' in result.stdout


@pytest.mark.parametrize("url", ["sqlite://", "sqlite:///:memory:"])
def test_worker_rechaza_base_no_durable(url):
    with pytest.raises(ValueError, match="persistente"):
        ConciliadorEmision(url, PolizasHttp("http://127.0.0.1:1", "prueba-local"))


def test_worker_acota_esperas_sin_cambiar_el_pool_del_api(database_url):
    normal = crear_sesiones(database_url)
    worker = crear_sesiones(database_url, pool_size=1, max_overflow=0, limitar_esperas=True)
    try:
        with normal() as api, worker() as background:
            if api.bind.dialect.name == "postgresql":
                assert api.scalar(text("SHOW statement_timeout")) == "0"
                assert api.scalar(text("SHOW lock_timeout")) == "0"
                assert background.scalar(text("SHOW statement_timeout")) == "5s"
                assert background.scalar(text("SHOW lock_timeout")) == "2s"
                assert api.scalar(text("SHOW TimeZone")) == background.scalar(text("SHOW TimeZone"))
                assert api.scalar(text("SELECT pg_backend_pid()")) != background.scalar(
                    text("SELECT pg_backend_pid()")
                )
            else:
                assert api.scalar(text("PRAGMA busy_timeout")) == 5000
                assert background.scalar(text("PRAGMA busy_timeout")) == 2000
    finally:
        normal.kw["bind"].dispose()
        worker.kw["bind"].dispose()
