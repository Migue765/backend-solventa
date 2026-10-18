from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from cotizacion.domain.cotizacion import CoberturaCotizada, Cotizacion
from cotizacion.domain.dinero import Dinero
from cotizacion.domain.suscripcion import (
    DecisionSuscripcion,
    EstadoSagaEmision,
    EstadoSuscripcion,
    SagaEmision,
    Suscripcion,
)
from cotizacion.infrastructure.persistence.database import crear_sesiones
from cotizacion.infrastructure.persistence.models import (
    CotizacionModel,
    DecisionSuscripcionModel,
    OutboxEventoModel,
    SagaEmisionModel,
    SuscripcionModel,
)
from cotizacion.infrastructure.persistence.repositorio_cotizaciones import (
    RepositorioCotizacionesSqlAlchemy,
)
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker


def cotizacion_calculada():
    c1 = CoberturaCotizada(uuid4(), Dinero(Decimal("1000"), "COP"), Dinero(Decimal("100"), "COP"))
    c2 = CoberturaCotizada(uuid4(), Dinero(Decimal("500"), "COP"), Dinero.cero("COP"))
    cotizacion = Cotizacion(cliente_id=uuid4(), producto_id=uuid4(), coberturas=[c1, c2])
    cotizacion.calcular(
        {
            c1.cobertura_id: Dinero(Decimal("13.50"), "COP"),
            c2.cobertura_id: Dinero(Decimal("7.50"), "COP"),
        },
        datetime(2026, 10, 1, tzinfo=timezone.utc),
        timedelta(days=30),
    )
    return cotizacion


def test_guarda_y_recupera_la_cotizacion(database_url):
    repositorio = RepositorioCotizacionesSqlAlchemy(crear_sesiones(database_url))
    original = cotizacion_calculada()

    repositorio.guardar(original)
    recuperada = repositorio.obtener(original.cotizacion_id)

    assert recuperada == original


def test_obtener_inexistente_devuelve_none(database_url):
    repositorio = RepositorioCotizacionesSqlAlchemy(crear_sesiones(database_url))

    assert repositorio.obtener(uuid4()) is None


@pytest.fixture
def sesiones_con_fk(database_url):
    engine = create_engine(database_url)

    @event.listens_for(engine, "connect")
    def activar_fk(conexion, _):
        conexion.execute("PRAGMA foreign_keys=ON")

    sesiones = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        with sesiones() as sesion:
            assert sesion.scalar(text("PRAGMA foreign_keys")) == 1
        yield sesiones
    finally:
        engine.dispose()


def nueva_suscripcion(cotizacion):
    ahora = datetime.now(timezone.utc)
    suscripcion_id = uuid4()
    return Suscripcion(
        suscripcion_id=suscripcion_id,
        solicitud_id="AQT-LOCAL-FK",
        cotizacion_id=cotizacion.cotizacion_id,
        cliente_id=cotizacion.cliente_id,
        oferta_id=uuid4(),
        oferta_version=1,
        estado=EstadoSuscripcion.APROBADA,
        regla_version="suscripcion-automatica-2026.1",
        creada_en=ahora,
        actualizada_en=ahora,
        idempotency_key=uuid4(),
        request_fingerprint="1" * 64,
        decision=DecisionSuscripcion(
            suscripcion_id=suscripcion_id,
            regla_version="suscripcion-automatica-2026.1",
            resultado=EstadoSuscripcion.APROBADA,
            datos_evaluados={"perfilValidado": True},
            hash_decision="0" * 64,
            decidida_en=ahora,
        ),
        saga=SagaEmision(
            suscripcion_id=suscripcion_id,
            estado=EstadoSagaEmision.PAGO_PENDIENTE,
            version=1,
            actualizada_en=ahora,
        ),
    )


def test_guarda_y_recupera_suscripcion_con_fk_activas(sesiones_con_fk):
    repositorio = RepositorioCotizacionesSqlAlchemy(sesiones_con_fk)
    cotizacion = cotizacion_calculada()
    repositorio.guardar(cotizacion)
    original = nueva_suscripcion(cotizacion)

    repositorio.guardar_suscripcion(original)

    assert repositorio.obtener_suscripcion(original.solicitud_id) == original
    assert (
        repositorio.buscar_suscripcion_por_idempotencia(
            original.cliente_id, original.idempotency_key
        )
        == original
    )
    with sesiones_con_fk() as sesion:
        evento = sesion.scalar(select(OutboxEventoModel))
        assert evento is not None
        assert evento.tipo == "SuscripcionAprobada"
        assert evento.agregado_id == original.suscripcion_id
        assert evento.payload == {
            "suscripcionId": str(original.suscripcion_id),
            "solicitudId": original.solicitud_id,
            "estado": original.estado.value,
            "reglaVersion": original.regla_version,
            "hashDecision": original.decision.hash_decision,
        }
        for modelo in (SuscripcionModel, DecisionSuscripcionModel, SagaEmisionModel):
            assert sesion.scalar(select(func.count()).select_from(modelo)) == 1


def test_error_posterior_al_flush_revierte_suscripcion_hijos_y_outbox(sesiones_con_fk):
    repositorio = RepositorioCotizacionesSqlAlchemy(sesiones_con_fk)
    cotizacion = cotizacion_calculada()
    repositorio.guardar(cotizacion)
    original = nueva_suscripcion(cotizacion)
    invalida = replace(original, saga=replace(original.saga, suscripcion_id=uuid4()))
    tablas_insertadas = []

    @event.listens_for(sesiones_con_fk.kw["bind"], "after_cursor_execute")
    def registrar_insercion(_, __, sentencia, ___, ____, _____):
        if sentencia.startswith("INSERT INTO "):
            tablas_insertadas.append(sentencia.split()[2])

    with pytest.raises(IntegrityError, match="FOREIGN KEY constraint failed"):
        repositorio.guardar_suscripcion(invalida)

    assert tablas_insertadas == ["suscripciones", "decisiones_suscripcion", "outbox_eventos"]
    assert repositorio.obtener_suscripcion(original.solicitud_id) is None
    assert (
        repositorio.buscar_suscripcion_por_idempotencia(
            original.cliente_id, original.idempotency_key
        )
        is None
    )
    with sesiones_con_fk() as sesion:
        for modelo in (
            SuscripcionModel,
            DecisionSuscripcionModel,
            SagaEmisionModel,
            OutboxEventoModel,
        ):
            assert sesion.scalar(select(func.count()).select_from(modelo)) == 0
        assert sesion.get(CotizacionModel, cotizacion.cotizacion_id) is not None

    repositorio.guardar_suscripcion(original)
    assert repositorio.obtener_suscripcion(original.solicitud_id) == original
