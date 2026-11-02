from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from alembic import command
from cotizacion.application.pagos import ProcesarPagoSimulado
from cotizacion.domain.errors import ConflictoIdempotenciaError, ResultadoPagoAmbiguoError
from cotizacion.domain.pago import (
    PagoSimulado,
    PagoSimuladoPendiente,
    PagoSimuladoRechazado,
)
from cotizacion.infrastructure.pago_simulado import SimuladorPago
from cotizacion.infrastructure.persistence.models import (
    ConciliacionEmisionModel,
    CotizacionModel,
    EfectoPagoSimuladoModel,
    IntentoPagoSimuladoModel,
    OutboxEventoModel,
    PagoSimuladoModel,
    SagaEmisionModel,
)
from cotizacion.infrastructure.persistence.repositorio_pagos import RepositorioPagosSqlAlchemy
from sqlalchemy import MetaData, Table, delete, event, func, inspect, select, text, update

from .test_pagos_simulados_api import pagar, preparar


@pytest.fixture
def caso(client):
    client.application.extensions["conciliador_emision"].cerrar()
    base, query, seleccion, suscripcion = preparar(client)
    repo = client.application.extensions["repositorio_pagos"]
    reloj = [datetime.now(UTC)]
    proveedor = SimuladorPago(repo._sesiones, "confirmado")
    servicio = ProcesarPagoSimulado(repo, proveedor, reloj=lambda: reloj[0])
    client.application.extensions["procesar_pago_simulado"] = servicio
    return SimpleNamespace(
        client=client,
        base=base,
        query=query,
        seleccion=seleccion,
        suscripcion=suscripcion,
        repo=repo,
        proveedor=proveedor,
        servicio=servicio,
        reloj=reloj,
        solicitud=base.rsplit("/", 1)[1],
        cliente=UUID(query["clienteId"]),
        clave=uuid4(),
    )


def contar(session, modelo):
    return session.scalar(select(func.count()).select_from(modelo))


def verificar_no_terminal(caso, efecto=None):
    with caso.repo._sesiones() as session:
        intento = session.scalars(select(IntentoPagoSimuladoModel)).one()
        assert intento.resultado == "pendiente" and intento.evaluado_en is None
        assert contar(session, PagoSimuladoModel) == 0
        assert contar(session, ConciliacionEmisionModel) == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(OutboxEventoModel)
                .where(OutboxEventoModel.tipo.like("PagoSimulado%"))
            )
            == 0
        )
        saga = session.get(SagaEmisionModel, UUID(caso.suscripcion["id"]))
        assert (saga.estado, saga.version) == ("pago_pendiente", 1)
        assert contar(session, EfectoPagoSimuladoModel) == (efecto is not None)
        if efecto is not None:
            assert session.scalar(select(EfectoPagoSimuladoModel.resultado)) == efecto
        return intento


@pytest.mark.parametrize("despues", [False, True], ids=["P02-antes", "P03-P04-despues"])
@pytest.mark.parametrize("terminal", ["confirmado", "rechazado"])
def test_perdida_recupera_mismo_efecto_y_no_reinterpreta_resultado(
    caso, monkeypatch, despues, terminal
):
    proveedor = SimuladorPago(caso.repo._sesiones, terminal)
    original = proveedor.ejecutar
    caso.servicio._proveedor = proveedor

    def perder(efecto_id, instantanea, huella):
        if despues:
            original(efecto_id, instantanea, huella)
        raise ResultadoPagoAmbiguoError("respuesta_perdida")

    monkeypatch.setattr(proveedor, "ejecutar", perder)
    respuesta = pagar(caso.client, caso.base, caso.query, str(caso.clave), json={})
    assert respuesta.status_code == 200
    pendiente = respuesta.get_json()
    assert pendiente["estado"] == "pendiente"
    assert pendiente["codigo"] == "resultado_no_confirmado"
    assert pendiente["saga"] == caso.suscripcion["saga"]
    assert pendiente["importe"] == caso.seleccion["prima"]
    assert pendiente["referenciaInterna"] == f"SIM-{pendiente['id']}"
    assert not {"confirmadaEn", "rechazadaEn", "motivo"} & pendiente.keys()
    intento = verificar_no_terminal(caso, terminal if despues else None)
    assert intento.ultimo_error == "respuesta_perdida" and intento.intentos_conciliacion == 1
    assert intento.lease_token is None
    assert (
        caso.client.get(
            caso.base + "/pago-simulado",
            query_string=caso.query,
            headers={"Idempotency-Key": str(caso.clave)},
        ).get_json()
        == pendiente
    )
    assert pagar(caso.client, caso.base, caso.query, str(caso.clave)).get_json() == pendiente
    configuracion = (
        ("rechazado" if terminal == "confirmado" else "confirmado") if despues else terminal
    )
    reiniciado = ProcesarPagoSimulado(
        caso.repo, SimuladorPago(caso.repo._sesiones, configuracion), reloj=lambda: caso.reloj[0]
    )
    caso.reloj[0] += timedelta(seconds=3)
    resultado = reiniciado.conciliar_uno()
    assert isinstance(
        resultado, PagoSimulado if terminal == "confirmado" else PagoSimuladoRechazado
    )
    assert str(resultado.pago_id) == pendiente["id"]
    with caso.repo._sesiones() as session:
        assert contar(session, IntentoPagoSimuladoModel) == 1
        assert contar(session, EfectoPagoSimuladoModel) == 1
        assert contar(session, PagoSimuladoModel) == (terminal == "confirmado")
        eventos = session.scalars(
            select(OutboxEventoModel).where(OutboxEventoModel.tipo.like("PagoSimulado%"))
        ).all()
        assert len(eventos) == 1
        assert eventos[0].tipo == (
            "PagoSimuladoConfirmado" if terminal == "confirmado" else "PagoSimuladoRechazado"
        )
    assert reiniciado.conciliar_uno() is None


def test_P07_P10_rival_no_aceptado_y_get_pendiente_sql_puro(caso, monkeypatch):
    pago, nuevo = caso.repo.reservar_pago_simulado(
        caso.solicitud, caso.cliente, caso.clave, caso.reloj[0]
    )
    assert nuevo and isinstance(pago, PagoSimuladoPendiente)
    sentencias = []
    engine = caso.repo._sesiones.kw["bind"]

    def registrar(_conn, _cursor, statement, _params, _context, _many):
        sentencias.append(statement.strip().split()[0].upper())

    def prohibido(*_):
        raise AssertionError("GET/replay/rival no llama a proveedor ni emite")

    monkeypatch.setattr(caso.proveedor, "consultar", prohibido)
    monkeypatch.setattr(caso.proveedor, "ejecutar", prohibido)
    monkeypatch.setattr("cotizacion.api.solicitudes.completar_emision_academica", prohibido)
    event.listen(engine, "before_cursor_execute", registrar)
    try:
        pendiente = caso.client.get(
            caso.base + "/pago-simulado", query_string=caso.query
        ).get_json()
        for _ in range(3):
            respuesta = caso.client.get(
                caso.base + "/pago-simulado",
                query_string=caso.query,
                headers={"Idempotency-Key": str(caso.clave)},
            )
            assert respuesta.status_code == 200 and respuesta.get_json() == pendiente
            assert respuesta.headers["Cache-Control"] == "no-store"
        dashboard = caso.client.application.extensions[
            "repositorio_cotizaciones"
        ].consultar_dashboard(caso.cliente)
        assert dashboard["pagos"][0]["id"] == str(pago.pago_id)
        assert dashboard["pagos"][0]["creadaEn"] == pendiente["creadaEn"]
        assert dashboard["pagos"][0]["estado"] == "pendiente"
    finally:
        event.remove(engine, "before_cursor_execute", registrar)
    assert sentencias and set(sentencias) == {"SELECT"}
    assert pagar(caso.client, caso.base, caso.query, str(caso.clave)).get_json() == pendiente
    rival = str(uuid4())
    respuesta = pagar(caso.client, caso.base, caso.query, rival)
    assert respuesta.status_code == 409
    assert respuesta.get_json()["error"] == "pago_en_proceso"
    assert respuesta.get_json()["pagoId"] == str(pago.pago_id)
    assert caso.client.get(respuesta.headers["Location"]).get_json() == pendiente
    assert (
        caso.client.get(
            caso.base + "/pago-simulado",
            query_string=caso.query,
            headers={"Idempotency-Key": rival},
        ).status_code
        == 404
    )
    verificar_no_terminal(caso)


def test_P08_lease_vencido_fencea_finalizacion_y_aplazamiento(caso):
    pago, _ = caso.repo.reservar_pago_simulado(
        caso.solicitud, caso.cliente, caso.clave, caso.reloj[0]
    )
    vieja = caso.repo.reclamar_pago_simulado(caso.reloj[0], pago.pago_id)
    assert vieja is not None
    caso.reloj[0] += timedelta(seconds=31)
    nueva = caso.repo.reclamar_pago_simulado(caso.reloj[0], pago.pago_id)
    assert nueva is not None and nueva.token != vieja.token
    terminal = caso.proveedor.ejecutar(pago.pago_id, vieja.pago, vieja.huella)
    assert caso.proveedor.ejecutar(pago.pago_id, nueva.pago, nueva.huella) == terminal
    assert isinstance(
        caso.repo.finalizar_pago_simulado(vieja, terminal, caso.reloj[0]), PagoSimuladoPendiente
    )
    assert not caso.repo.aplazar_pago_simulado(vieja, caso.reloj[0], "respuesta_perdida")
    verificar_no_terminal(caso, "confirmado")
    ganador = caso.repo.finalizar_pago_simulado(nueva, terminal, caso.reloj[0])
    assert isinstance(ganador, PagoSimulado)
    assert caso.repo.finalizar_pago_simulado(vieja, terminal, caso.reloj[0]) == ganador
    with caso.repo._sesiones() as session:
        assert contar(session, EfectoPagoSimuladoModel) == 1
        assert contar(session, PagoSimuladoModel) == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(OutboxEventoModel)
                .where(OutboxEventoModel.tipo == "PagoSimuladoConfirmado")
            )
            == 1
        )
    with pytest.raises(ConflictoIdempotenciaError):
        caso.proveedor.consultar(pago.pago_id, "0" * 64)
    with pytest.raises(ConflictoIdempotenciaError):
        caso.proveedor.ejecutar(pago.pago_id, nueva.pago, "0" * 64)


@pytest.mark.parametrize("terminal", ["confirmado", "rechazado"])
def test_P09_outbox_falla_sin_deshacer_reserva_ni_efecto(caso, terminal):
    caso.servicio._proveedor = SimuladorPago(caso.repo._sesiones, terminal)

    def fallar(*_):
        raise RuntimeError("fallo T2 controlado")

    event.listen(OutboxEventoModel, "before_insert", fallar)
    try:
        with pytest.raises(RuntimeError, match="T2"):
            caso.servicio.confirmar(caso.solicitud, caso.cliente, caso.clave)
    finally:
        event.remove(OutboxEventoModel, "before_insert", fallar)
    intento = verificar_no_terminal(caso, terminal)
    assert intento.idempotency_key == caso.clave and intento.lease_token is not None
    caso.reloj[0] += timedelta(seconds=31)
    recuperado = caso.servicio.conciliar_uno()
    assert isinstance(
        recuperado, PagoSimulado if terminal == "confirmado" else PagoSimuladoRechazado
    )
    assert recuperado.pago_id == intento.intento_id
    assert caso.servicio.conciliar_uno() is None


def test_P10_P11_rechazo_anterior_no_oculta_pendiente_y_selector_conserva_historia(caso):
    caso.servicio._proveedor = SimuladorPago(caso.repo._sesiones, "rechazado")
    rechazo = caso.servicio.confirmar(caso.solicitud, caso.cliente, caso.clave)
    nueva_clave = uuid4()
    pendiente, nuevo = caso.repo.reservar_pago_simulado(
        caso.solicitud, caso.cliente, nueva_clave, caso.reloj[0]
    )
    assert nuevo and isinstance(pendiente, PagoSimuladoPendiente)
    assert caso.servicio.consultar(caso.solicitud, caso.cliente) == pendiente
    dashboard = caso.client.application.extensions["repositorio_cotizaciones"].consultar_dashboard(
        caso.cliente
    )
    assert dashboard["pagos"][0]["id"] == str(pendiente.pago_id)
    assert dashboard["pagos"][0]["estado"] == "pendiente"
    caso.servicio._proveedor = caso.proveedor
    caso.reloj[0] += timedelta(seconds=3)
    confirmado = caso.servicio.conciliar_uno()
    assert isinstance(confirmado, PagoSimulado)
    assert caso.servicio.consultar(caso.solicitud, caso.cliente, caso.clave) == rechazo
    assert caso.servicio.consultar(caso.solicitud, caso.cliente, nueva_clave) == confirmado
    assert caso.servicio.confirmar(caso.solicitud, caso.cliente, caso.clave) == rechazo
    assert caso.servicio.confirmar(caso.solicitud, caso.cliente, uuid4()) == confirmado
    assert caso.servicio.consultar(caso.solicitud, caso.cliente) == confirmado


def test_P12_reserva_autorizada_se_recupera_despues_de_expirar(caso):
    pago, _ = caso.repo.reservar_pago_simulado(
        caso.solicitud, caso.cliente, caso.clave, caso.reloj[0]
    )
    with caso.repo._sesiones.begin() as session:
        session.execute(
            update(CotizacionModel).values(valida_hasta=caso.reloj[0] - timedelta(days=1))
        )
    caso.reloj[0] += timedelta(seconds=3)
    resultado = caso.servicio.conciliar_uno()
    assert isinstance(resultado, PagoSimulado) and resultado.pago_id == pago.pago_id
    assert caso.servicio.confirmar(caso.solicitud, caso.cliente, caso.clave) == resultado
    base, query, _, _ = preparar(caso.client)
    with caso.repo._sesiones.begin() as session:
        session.execute(
            update(CotizacionModel).values(valida_hasta=caso.reloj[0] - timedelta(days=1))
        )
    assert pagar(caso.client, base, query).status_code == 422


def test_P13_selector_no_filtra_ajenos_y_rechaza_cruces(caso):
    caso.repo.reservar_pago_simulado(caso.solicitud, caso.cliente, caso.clave, caso.reloj[0])
    headers = {"Idempotency-Key": str(caso.clave)}
    assert (
        caso.client.get(
            caso.base + "/pago-simulado", query_string={"clienteId": str(uuid4())}, headers=headers
        ).status_code
        == 404
    )
    otra, _, _, _ = preparar(caso.client, str(caso.cliente))
    assert (
        caso.client.get(
            otra + "/pago-simulado", query_string=caso.query, headers=headers
        ).status_code
        == 409
    )
    for selector in ("", "invalida"):
        assert (
            caso.client.get(
                caso.base + "/pago-simulado",
                query_string=caso.query,
                headers={"Idempotency-Key": selector},
            ).status_code
            == 400
        )
    verificar_no_terminal(caso)


@pytest.mark.parametrize("invalida", [False, True])
def test_P17_indisponibilidad_no_fuerza_terminal_ni_libera_identidad(
    caso, monkeypatch, caplog, invalida
):
    def no_disponible(*_):
        if invalida:
            return {"estado": "confirmado"}
        raise ResultadoPagoAmbiguoError("proveedor_no_disponible")

    def ejecutar_prohibido(*_):
        raise AssertionError("No ejecutar cuando la consulta no acredita ausencia")

    monkeypatch.setattr(caso.proveedor, "consultar", no_disponible)
    monkeypatch.setattr(caso.proveedor, "ejecutar", ejecutar_prohibido)
    resultado = caso.servicio.confirmar(caso.solicitud, caso.cliente, caso.clave)
    assert isinstance(resultado, PagoSimuladoPendiente)
    for _ in range(11):
        caso.reloj[0] += timedelta(seconds=31)
        assert caso.servicio.conciliar_uno() == resultado
    intento = verificar_no_terminal(caso)
    assert intento.intentos_conciliacion == 12
    assert intento.ultimo_error == ("respuesta_invalida" if invalida else "proveedor_no_disponible")
    assert intento.ultimo_error in caplog.text
    assert pagar(caso.client, caso.base, caso.query).status_code == 409
    assert caso.servicio.consultar(caso.solicitud, caso.cliente, caso.clave) == resultado


def test_P16_downgrade_no_borra_reserva_ni_efecto(caso, alembic_config):
    resultado = caso.servicio.confirmar(caso.solicitud, caso.cliente, caso.clave)
    with pytest.raises(RuntimeError, match="reservas y efectos"):
        command.downgrade(alembic_config, "selecciones_provisionales")
    with caso.repo._sesiones() as session:
        assert (
            session.scalar(text("SELECT version_num FROM alembic_version"))
            == "pagos_simulados_pendientes"
        )
        assert contar(session, IntentoPagoSimuladoModel) == 1
        assert contar(session, EfectoPagoSimuladoModel) == 1
    assert caso.servicio.consultar(caso.solicitud, caso.cliente) == resultado


@pytest.mark.parametrize("fase", ["T0", "proveedor"])
def test_error_no_tipado_no_inventa_reserva_ni_exito(caso, monkeypatch, fase):
    def fallar(*_):
        raise RuntimeError("error no ambiguo controlado")

    if fase == "T0":
        event.listen(IntentoPagoSimuladoModel, "before_insert", fallar)
    else:
        monkeypatch.setattr(caso.proveedor, "consultar", fallar)
    try:
        respuesta = pagar(caso.client, caso.base, caso.query, str(caso.clave), json={})
        assert respuesta.status_code == 500
        assert respuesta.get_json()["error"] == "error_interno"
    finally:
        if fase == "T0":
            event.remove(IntentoPagoSimuladoModel, "before_insert", fallar)
    with caso.repo._sesiones() as session:
        assert contar(session, IntentoPagoSimuladoModel) == (fase == "proveedor")
        assert contar(session, EfectoPagoSimuladoModel) == 0
        assert contar(session, PagoSimuladoModel) == 0
    if fase == "proveedor":
        assert caso.servicio.consultar(caso.solicitud, caso.cliente).pago_id is not None
    else:
        assert (
            caso.client.get(caso.base + "/pago-simulado", query_string=caso.query).status_code
            == 404
        )


@pytest.mark.parametrize("habilitada", [False, True])
def test_P16_eligibilidad_se_congela_en_reserva_no_en_recuperacion(caso, habilitada):
    repo_original = RepositorioPagosSqlAlchemy(
        caso.repo._sesiones, emitir_polizas=habilitada, conciliar_emision=habilitada
    )
    pago, _ = repo_original.reservar_pago_simulado(
        caso.solicitud, caso.cliente, caso.clave, caso.reloj[0]
    )
    nuevo_repo = RepositorioPagosSqlAlchemy(
        caso.repo._sesiones, emitir_polizas=not habilitada, conciliar_emision=not habilitada
    )
    caso.reloj[0] += timedelta(seconds=3)
    resultado = ProcesarPagoSimulado(
        nuevo_repo, caso.proveedor, reloj=lambda: caso.reloj[0]
    ).conciliar_uno()
    assert isinstance(resultado, PagoSimulado) and resultado.pago_id == pago.pago_id
    with caso.repo._sesiones() as session:
        assert session.get(PagoSimuladoModel, pago.pago_id).emision_solicitada == habilitada
        assert contar(session, ConciliacionEmisionModel) == habilitada
    assert (nuevo_repo.evidencia_emision(resultado) is not None) == habilitada


@pytest.mark.parametrize("terminal", ["confirmado", "rechazado"])
@pytest.mark.parametrize("duplicado", [False, True])
def test_P16_migracion_backfill_conserva_historia_o_rechaza_duplicados(
    caso, alembic_config, terminal, duplicado
):
    caso.servicio._proveedor = SimuladorPago(caso.repo._sesiones, terminal)
    resultado = caso.servicio.confirmar(caso.solicitud, caso.cliente, caso.clave)
    engine = caso.repo._sesiones.kw["bind"]
    # Semilla legacy sin ledger: se verifica la migracion sobre su esquema real anterior.
    with caso.repo._sesiones.begin() as session:
        session.execute(delete(EfectoPagoSimuladoModel))
        session.execute(update(IntentoPagoSimuladoModel).values(huella=None))
    command.downgrade(alembic_config, "selecciones_provisionales")
    legacy = Table("intentos_pago_simulado", MetaData(), autoload_with=engine)
    with engine.begin() as connection:
        original = dict(connection.execute(select(legacy)).mappings().one())
        if duplicado:
            identity, key = uuid4(), uuid4()
            connection.execute(
                legacy.insert().values(
                    {
                        **original,
                        "intento_id": identity.hex if engine.dialect.name == "sqlite" else identity,
                        "idempotency_key": key.hex if engine.dialect.name == "sqlite" else key,
                    }
                )
            )
        anteriores = [
            dict(row)
            for row in connection.execute(select(legacy).order_by(legacy.c.intento_id)).mappings()
        ]
        eventos = connection.scalar(select(func.count()).select_from(OutboxEventoModel))
    if duplicado and terminal == "confirmado":
        with pytest.raises(RuntimeError, match="confirmados duplicados"):
            command.upgrade(alembic_config, "head")
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == "selecciones_provisionales"
            )
            assert [
                dict(row)
                for row in connection.execute(
                    select(legacy).order_by(legacy.c.intento_id)
                ).mappings()
            ] == anteriores
        assert "efectos_pago_simulado" not in inspect(engine).get_table_names()
        return
    command.upgrade(alembic_config, "head")
    with engine.connect() as connection:
        assert [
            dict(row)
            for row in connection.execute(select(legacy).order_by(legacy.c.intento_id)).mappings()
        ] == anteriores
        assert connection.scalar(select(func.count()).select_from(OutboxEventoModel)) == eventos
    with caso.repo._sesiones() as session:
        for intento in session.scalars(select(IntentoPagoSimuladoModel)):
            assert intento.creada_en == intento.evaluado_en
            assert intento.huella is None
            assert not intento.emision_solicitada and not intento.conciliacion_emision_solicitada
        assert contar(session, EfectoPagoSimuladoModel) == 0
        assert contar(session, ConciliacionEmisionModel) == 0
    assert caso.servicio.consultar(caso.solicitud, caso.cliente, caso.clave) == resultado
