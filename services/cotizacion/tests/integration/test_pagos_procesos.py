import json
import os
import subprocess
import sys
from contextlib import contextmanager
from queue import Queue
from threading import Thread
from uuid import uuid4

import pytest
from cotizacion.infrastructure.persistence.models import (
    ConciliacionEmisionModel,
    EfectoPagoSimuladoModel,
    IntentoPagoSimuladoModel,
    OutboxEventoModel,
    PagoSimuladoModel,
)
from sqlalchemy import func, select

from .test_pagos_simulados_api import preparar

PROCESO = """
import json, os, sys, time
from datetime import UTC, datetime, timedelta
from uuid import UUID
from cotizacion.app import create_app
from cotizacion.config import Config
from cotizacion.api.schemas import pago_simulado_a_json
from cotizacion.application.pagos import ProcesarPagoSimulado
from cotizacion.domain.pago import PagoSimuladoPendiente
from cotizacion.infrastructure.pago_simulado import SimuladorPago
from cotizacion.infrastructure.persistence.database import crear_sesiones
from cotizacion.infrastructure.persistence.repositorio_pagos import RepositorioPagosSqlAlchemy

def mensaje(**datos):
    print(json.dumps({"pid": os.getpid(), **datos}), flush=True)

def barrera(etapa, pago=None):
    mensaje(etapa=etapa, id=str(pago.pago_id) if pago else None)
    assert sys.stdin.readline().strip() == "continuar"

modo = os.environ["PAYMENT_STAGE"]
url = os.environ["OWNED_DB_URL"]
solicitud = os.environ["PAYMENT_REQUEST"]
cliente = UUID(os.environ["PAYMENT_CUSTOMER"])
clave = UUID(os.environ["PAYMENT_KEY"])
terminal = os.environ["PAYMENT_RESULT"]
if modo == "recuperar":
    app = create_app(Config(database_url=url, pago_simulado_resultado=terminal))
    worker = app.extensions["conciliador_emision"]
    try:
        assert worker.activo and app.extensions["polizas"] is None
        assert not app.extensions["cotizacion_config"].conciliacion_emision_habilitada
        mensaje(etapa="recuperador", activo=worker.activo)
        limite = time.monotonic() + 45
        servicio = app.extensions["procesar_pago_simulado"]
        while time.monotonic() < limite:
            pago = servicio.consultar(solicitud, cliente, clave)
            if not isinstance(pago, PagoSimuladoPendiente):
                mensaje(etapa="resultado", pago=pago_simulado_a_json(pago))
                break
            time.sleep(0.05)
        else:
            raise AssertionError("El lifecycle no recupero el intento durable")
    finally:
        worker.cerrar()
        app.extensions["repositorio_pagos"]._sesiones.kw["bind"].dispose()
else:
    sesiones = crear_sesiones(url)
    repo = RepositorioPagosSqlAlchemy(sesiones)
    proveedor = SimuladorPago(sesiones, terminal)
    try:
        if modo == "carrera":
            original = repo._existente
            lecturas = [0]
            def observar(*args):
                encontrado = original(*args)
                lecturas[0] += 1
                if lecturas[0] == 1:
                    assert encontrado is None
                    barrera("antes_guardia_SQL")
                return encontrado
            repo._existente = observar
            barrera("listo")
            pago = ProcesarPagoSimulado(repo, proveedor).confirmar(solicitud, cliente, clave)
            mensaje(etapa="resultado", pago=pago_simulado_a_json(pago))
        elif modo in {"efecto_viejo", "efecto_nuevo"}:
            ahora = datetime.now(UTC)
            if modo == "efecto_viejo":
                ahora -= timedelta(seconds=31)
            pago = repo.obtener_pago_simulado(solicitud, cliente, clave)
            reserva = repo.reclamar_pago_simulado(ahora, pago.pago_id)
            assert reserva is not None
            barrera(modo, pago)
            resultado = proveedor.ejecutar(pago.pago_id, reserva.pago, reserva.huella)
            pago = repo.finalizar_pago_simulado(reserva, resultado, datetime.now(UTC))
            mensaje(etapa="resultado", pago=pago_simulado_a_json(pago))
        else:
            pago, nuevo = repo.reservar_pago_simulado(solicitud, cliente, clave, datetime.now(UTC))
            assert nuevo
            if modo == "T0":
                barrera("T0", pago)
            reserva = repo.reclamar_pago_simulado(datetime.now(UTC), pago.pago_id)
            assert reserva is not None
            resultado = proveedor.ejecutar(pago.pago_id, reserva.pago, reserva.huella)
            if modo == "T1":
                barrera("T1", pago)
            pago = repo.finalizar_pago_simulado(reserva, resultado, datetime.now(UTC))
            if modo == "T2":
                barrera("T2", pago)
    finally:
        sesiones.kw["bind"].dispose()
"""


@contextmanager
def proceso(tmp_path, database_url, base, query, clave, etapa, resultado="confirmado"):
    with (tmp_path / f"proceso-{uuid4().hex}.log").open("w", encoding="utf-8") as log:
        hijo = subprocess.Popen(
            [sys._base_executable if os.name == "nt" else sys.executable, "-c", PROCESO],
            env={
                **os.environ,
                "PYTHONPATH": os.pathsep.join(sys.path),
                "PYTHONDONTWRITEBYTECODE": "1",
                "OWNED_DB_URL": database_url,
                "PAYMENT_REQUEST": base.rsplit("/", 1)[1],
                "PAYMENT_CUSTOMER": query["clienteId"],
                "PAYMENT_KEY": str(clave),
                "PAYMENT_STAGE": etapa,
                "PAYMENT_RESULT": resultado,
            },
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=log,
            text=True,
        )
        try:
            yield hijo
        finally:
            if hijo.poll() is None:
                hijo.kill()
            hijo.wait(timeout=10)
            hijo.stdin.close()
            hijo.stdout.close()


def recibir(hijo):
    mensajes = Queue()
    Thread(target=lambda: mensajes.put(hijo.stdout.readline()), daemon=True).start()
    linea = mensajes.get(timeout=50)
    assert linea, f"El proceso {hijo.pid} termino antes de emitir su barrera"
    mensaje = json.loads(linea)
    assert mensaje["pid"] == hijo.pid
    return mensaje


def verificar_final(factory, terminal):
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(IntentoPagoSimuladoModel)) == 1
        assert session.scalar(select(func.count()).select_from(EfectoPagoSimuladoModel)) == 1
        assert session.scalar(select(func.count()).select_from(PagoSimuladoModel)) == (
            terminal == "confirmado"
        )
        assert session.scalar(select(func.count()).select_from(ConciliacionEmisionModel)) == 0
        assert session.scalar(select(IntentoPagoSimuladoModel.resultado)) == terminal
        eventos = session.scalars(
            select(OutboxEventoModel).where(OutboxEventoModel.tipo.like("PagoSimulado%"))
        ).all()
        assert len(eventos) == 1
        assert eventos[0].tipo == (
            "PagoSimuladoConfirmado" if terminal == "confirmado" else "PagoSimuladoRechazado"
        )


@pytest.mark.parametrize(
    ("frontera", "terminal"), [("T0", "confirmado"), ("T1", "rechazado"), ("T2", "confirmado")]
)
def test_P05_P16_reinicio_real_y_recuperacion_sin_emision(
    client, database_url, tmp_path, frontera, terminal
):
    client.application.extensions["conciliador_emision"].cerrar()
    base, query, _, _ = preparar(client)
    clave = uuid4()
    factory = client.application.extensions["repositorio_pagos"]._sesiones
    with proceso(tmp_path, database_url, base, query, clave, frontera, terminal) as hijo:
        barrera = recibir(hijo)
        assert barrera["etapa"] == frontera
        with factory() as session:
            intento = session.scalars(select(IntentoPagoSimuladoModel)).one()
            assert str(intento.intento_id) == barrera["id"] and intento.idempotency_key == clave
            assert session.scalar(select(func.count()).select_from(EfectoPagoSimuladoModel)) == (
                frontera != "T0"
            )
            assert intento.resultado == (terminal if frontera == "T2" else "pendiente")
        hijo.kill()
        hijo.wait(timeout=10)
        assert hijo.returncode not in (0, None)
        pid_anterior = hijo.pid
    escenario_nuevo = "confirmado" if frontera == "T1" else terminal
    with proceso(
        tmp_path, database_url, base, query, clave, "recuperar", escenario_nuevo
    ) as reiniciado:
        assert reiniciado.pid != pid_anterior
        inicio = recibir(reiniciado)
        assert inicio["etapa"] == "recuperador" and inicio["activo"] is True
        final = recibir(reiniciado)["pago"]
        assert final["id"] == barrera["id"] and final["estado"] == terminal
        assert reiniciado.wait(timeout=10) == 0
    verificar_final(factory, terminal)


def test_P06_misma_clave_dos_procesos_con_barrera_SQL(client, database_url, tmp_path):
    client.application.extensions["conciliador_emision"].cerrar()
    base, query, _, _ = preparar(client)
    clave = uuid4()
    factory = client.application.extensions["repositorio_pagos"]._sesiones
    with (
        proceso(tmp_path, database_url, base, query, clave, "carrera") as primero,
        proceso(tmp_path, database_url, base, query, clave, "carrera") as segundo,
    ):
        assert primero.pid != segundo.pid
        assert recibir(primero)["etapa"] == recibir(segundo)["etapa"] == "listo"
        for hijo in (primero, segundo):
            hijo.stdin.write("continuar\n")
            hijo.stdin.flush()
        for hijo in (primero, segundo):
            assert recibir(hijo)["etapa"] == "antes_guardia_SQL"
        for hijo in (primero, segundo):
            hijo.stdin.write("continuar\n")
            hijo.stdin.flush()
        respuestas = [recibir(hijo)["pago"] for hijo in (primero, segundo)]
        for hijo in (primero, segundo):
            assert hijo.wait(timeout=15) == 0
    assert respuestas[0]["id"] == respuestas[1]["id"]
    assert {r["estado"] for r in respuestas} <= {"pendiente", "confirmado"}
    assert any(r["estado"] == "confirmado" for r in respuestas)
    verificar_final(factory, "confirmado")
    with factory() as session:
        assert session.scalar(select(IntentoPagoSimuladoModel.intentos_conciliacion)) == 1


def test_P08_dos_procesos_efecto_unico_y_token_vencido_no_finaliza(client, database_url, tmp_path):
    from datetime import UTC, datetime
    from uuid import UUID

    client.application.extensions["conciliador_emision"].cerrar()
    base, query, _, _ = preparar(client)
    clave = uuid4()
    repo = client.application.extensions["repositorio_pagos"]
    pago, nuevo = repo.reservar_pago_simulado(
        base.rsplit("/", 1)[1], UUID(query["clienteId"]), clave, datetime.now(UTC)
    )
    assert nuevo
    with proceso(tmp_path, database_url, base, query, clave, "efecto_viejo", "rechazado") as viejo:
        assert recibir(viejo)["etapa"] == "efecto_viejo"
        with repo._sesiones() as session:
            token_viejo = session.get(IntentoPagoSimuladoModel, pago.pago_id).lease_token
        with proceso(tmp_path, database_url, base, query, clave, "efecto_nuevo") as nuevo_proceso:
            assert recibir(nuevo_proceso)["etapa"] == "efecto_nuevo"
            with repo._sesiones() as session:
                token_nuevo = session.get(IntentoPagoSimuladoModel, pago.pago_id).lease_token
            assert token_nuevo != token_viejo
            for hijo in (viejo, nuevo_proceso):
                hijo.stdin.write("continuar\n")
                hijo.stdin.flush()
            respuestas = [recibir(hijo)["pago"] for hijo in (viejo, nuevo_proceso)]
            for hijo in (viejo, nuevo_proceso):
                assert hijo.wait(timeout=15) == 0
    final = respuestas[1]
    assert final["estado"] in {"confirmado", "rechazado"}
    assert all(r["id"] == str(pago.pago_id) for r in respuestas)
    if respuestas[0]["estado"] != "pendiente":
        assert respuestas[0] == final
    verificar_final(repo._sesiones, final["estado"])
    with repo._sesiones() as session:
        assert session.scalar(select(IntentoPagoSimuladoModel.intentos_conciliacion)) == 2
