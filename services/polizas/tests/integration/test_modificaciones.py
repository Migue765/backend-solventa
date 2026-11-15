from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from flask import Flask
from flask.testing import FlaskClient
from polizas.app import create_app
from polizas.infrastructure.models import (
    AuditoriaPolizaModel,
    EndosoModel,
    PolizaModel,
    VersionPolizaModel,
)
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

TOKEN = "polizas-test-internal"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


class Reloj:
    def __init__(self) -> None:
        self.ahora = datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.ahora

    def dia(self, dia: date) -> None:
        self.ahora = datetime.combine(dia, time(15), UTC)


@pytest.fixture
def url(tmp_path: Path) -> str:
    url = f"sqlite:///{tmp_path / 'polizas.db'}"
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    return url


@pytest.fixture
def reloj() -> Reloj:
    return Reloj()


@pytest.fixture
def app(url: str, reloj: Reloj) -> Flask:
    return create_app(url, TOKEN, reloj=reloj)


def _emitir(cliente: FlaskClient, cliente_id: str, tipo: str = "automotive") -> dict[str, Any]:
    fechas = None
    if tipo == "travel":
        hoy = datetime.now(UTC).date()
        fechas = {"inicio": hoy.isoformat(), "fin": (hoy + timedelta(days=10)).isoformat()}
    response = cliente.post(
        "/v1/polizas",
        json={
            "clienteId": cliente_id,
            "suscripcionId": str(uuid4()),
            "pagoId": str(uuid4()),
            "tipo": tipo,
            "confirmadaEn": datetime.now(UTC).isoformat(),
            "simulada": True,
            "fechasContratadas": fechas,
        },
        headers=HEADERS,
    )
    assert response.status_code == 200
    poliza: dict[str, Any] = response.get_json()
    return poliza


def _cabeceras(cliente_id: str, clave: str | None = None) -> dict[str, str]:
    cabeceras = {**HEADERS, "X-Cliente-ID": cliente_id}
    if clave is not None:
        cabeceras["Idempotency-Key"] = clave
    return cabeceras


def _consultar(cliente: FlaskClient, poliza_id: str, cliente_id: str) -> Any:
    return cliente.get(f"/v1/polizas/{poliza_id}/modificacion", headers=_cabeceras(cliente_id))


def _simular(cliente: FlaskClient, poliza_id: str, cliente_id: str, cuerpo: object) -> Any:
    return cliente.post(
        f"/v1/polizas/{poliza_id}/modificaciones/simulacion",
        json=cuerpo,
        headers=_cabeceras(cliente_id),
    )


def _confirmar(
    cliente: FlaskClient, poliza_id: str, cliente_id: str, clave: str, cuerpo: object
) -> Any:
    return cliente.post(
        f"/v1/polizas/{poliza_id}/modificaciones",
        json=cuerpo,
        headers=_cabeceras(cliente_id, clave),
    )


def _contar(url: str, modelo: type[Any]) -> int:
    with Session(create_engine(url)) as sesion:
        return int(sesion.scalar(select(func.count()).select_from(modelo)) or 0)


PREMIUM_SIN_ASISTENCIA = {"versionBase": 1, "plan": "premium", "coberturasOpcionales": []}


def test_consulta_version_vigente_opciones_y_elegibilidad(app: Flask, reloj: Reloj) -> None:
    cliente = app.test_client()
    usuario = str(uuid4())
    poliza = _emitir(cliente, usuario)

    respuesta = _consultar(cliente, poliza["id"], usuario)
    assert respuesta.status_code == 200
    assert respuesta.headers["Cache-Control"] == "no-store"
    estado = respuesta.get_json()
    assert estado["modificable"] is True and estado["motivo"] is None
    actual = estado["versionActual"]
    assert actual["version"] == 1 and actual["plan"] == "esencial"
    assert actual["origen"] == "emision" and actual["prima"]["monto"] == "85000"
    assert [c["codigo"] for c in actual["coberturas"] if c["opcional"]] == ["asistencia_carretera"]
    assert all(c["incluida"] for c in actual["coberturas"])
    assert [p["codigo"] for p in estado["opciones"]["planes"]] == [
        "esencial",
        "integral",
        "premium",
    ]
    assert estado["opciones"]["coberturasOpcionales"][0]["recargo"]["monto"] == "8000"
    assert estado["historial"] == [actual]

    reloj.dia(date.fromisoformat(poliza["fechaFin"]))
    assert _consultar(cliente, poliza["id"], usuario).get_json()["motivo"] == "fin_de_vigencia"
    reloj.dia(date.fromisoformat(poliza["fechaFin"]) + timedelta(days=1))
    vencida = _consultar(cliente, poliza["id"], usuario).get_json()
    assert vencida["motivo"] == "poliza_vencida" and vencida["opciones"] is None
    assert vencida["versionActual"]["version"] == 1

    viaje = _emitir(cliente, usuario, "travel")
    reloj.dia(date.fromisoformat(viaje["fechaInicio"]))
    fechada = _consultar(cliente, viaje["id"], usuario).get_json()
    assert fechada["motivo"] == "producto_no_modificable"
    assert fechada["versionActual"] is None and fechada["historial"] == []

    assert _consultar(cliente, poliza["id"], str(uuid4())).status_code == 404
    assert cliente.get(f"/v1/polizas/{poliza['id']}/modificacion", headers=HEADERS).status_code == (
        400
    )


def test_simulacion_devuelve_impacto_sin_persistir(app: Flask, url: str, reloj: Reloj) -> None:
    cliente = app.test_client()
    usuario = str(uuid4())
    poliza = _emitir(cliente, usuario)
    inicio, fin = date.fromisoformat(poliza["fechaInicio"]), date.fromisoformat(poliza["fechaFin"])
    reloj.dia(inicio + timedelta(days=4))

    respuesta = _simular(cliente, poliza["id"], usuario, PREMIUM_SIN_ASISTENCIA)
    assert respuesta.status_code == 200
    impacto = respuesta.get_json()
    assert impacto["primaAnterior"] == {"monto": "85000", "moneda": "COP"}
    assert impacto["primaNueva"] == {"monto": "100100", "moneda": "COP"}
    assert impacto["diferenciaMensual"]["monto"] == "15100"
    efectivo = inicio + timedelta(days=5)
    restantes = (fin - efectivo).days + 1
    esperado = round(15100 * restantes / ((fin - inicio).days + 1))
    assert impacto["ajusteProrrateado"]["monto"] == str(esperado)
    assert impacto["diasRestantes"] == restantes
    assert impacto["efectivoDesde"] == efectivo.isoformat()
    assert impacto["vigencia"] == {
        "inicio": inicio.isoformat(),
        "fin": fin.isoformat(),
        "cambia": False,
    }
    assert impacto["coberturasRetiradas"] == ["asistencia_carretera"]
    assert impacto["coberturasAgregadas"] == []
    assert impacto["plan"] == {"anterior": "esencial", "nuevo": "premium"}
    assert len(impacto["impactoHash"]) == 64

    obligatoria = {**PREMIUM_SIN_ASISTENCIA, "coberturasOpcionales": ["danos_vehiculo"]}
    rechazo = _simular(cliente, poliza["id"], usuario, obligatoria)
    assert rechazo.status_code == 422
    assert rechazo.get_json()["error"] == "cambio_no_permitido"
    assert rechazo.get_json()["campo"] == "coberturasOpcionales.danos_vehiculo"
    for invalido in (
        {**PREMIUM_SIN_ASISTENCIA, "fechaFin": "2030-01-01"},
        {**PREMIUM_SIN_ASISTENCIA, "plan": "oro"},
        {**PREMIUM_SIN_ASISTENCIA, "versionBase": "1"},
        {**PREMIUM_SIN_ASISTENCIA, "coberturasOpcionales": ["a", "a"]},
    ):
        assert _simular(cliente, poliza["id"], usuario, invalido).status_code == 400
    igual = {"versionBase": 1, "plan": "esencial", "coberturasOpcionales": ["asistencia_carretera"]}
    assert _simular(cliente, poliza["id"], usuario, igual).get_json()["error"] == "sin_cambios"
    desactualizada = _simular(
        cliente, poliza["id"], usuario, {**PREMIUM_SIN_ASISTENCIA, "versionBase": 3}
    )
    assert desactualizada.status_code == 409
    assert desactualizada.get_json()["versionActual"] == 1
    assert _contar(url, VersionPolizaModel) == 0
    assert _contar(url, AuditoriaPolizaModel) == 0


def test_confirmacion_versiona_conserva_historial_y_es_idempotente(
    app: Flask, url: str, reloj: Reloj
) -> None:
    cliente = app.test_client()
    usuario = str(uuid4())
    poliza = _emitir(cliente, usuario)
    reloj.dia(date.fromisoformat(poliza["fechaInicio"]) + timedelta(days=2))
    impacto = _simular(cliente, poliza["id"], usuario, PREMIUM_SIN_ASISTENCIA).get_json()
    cuerpo = {**PREMIUM_SIN_ASISTENCIA, "confirma": True, "impactoHash": impacto["impactoHash"]}
    clave = str(uuid4())

    for sin_confirmar in ({**cuerpo, "confirma": False}, {**cuerpo, "confirma": "si"}):
        assert _confirmar(cliente, poliza["id"], usuario, clave, sin_confirmar).status_code == 400
    sin_clave = cliente.post(
        f"/v1/polizas/{poliza['id']}/modificaciones", json=cuerpo, headers=_cabeceras(usuario)
    )
    assert sin_clave.status_code == 400

    primera = _confirmar(cliente, poliza["id"], usuario, clave, cuerpo)
    assert primera.status_code == 201
    resultado = primera.get_json()
    assert resultado["versionAnterior"] == 1 and resultado["versionNueva"] == 2
    assert resultado["version"]["plan"] == "premium"
    assert resultado["version"]["prima"]["monto"] == "100100"
    assert resultado["version"]["origen"] == "modificacion"
    assert resultado["version"]["endosoId"] == resultado["endosoId"]
    assert resultado["impacto"] == impacto

    replay = _confirmar(cliente, poliza["id"], usuario, clave, cuerpo)
    assert replay.status_code == 200 and replay.get_json() == resultado
    otro = {**cuerpo, "plan": "integral"}
    assert _confirmar(cliente, poliza["id"], usuario, clave, otro).get_json()["error"] == (
        "idempotencia_conflictiva"
    )

    estado = _consultar(cliente, poliza["id"], usuario).get_json()
    assert estado["versionActual"] == resultado["version"]
    assert [v["version"] for v in estado["historial"]] == [2, 1]
    assert estado["historial"][1]["plan"] == "esencial"
    assert estado["historial"][1]["prima"]["monto"] == "85000"

    obsoleta = _confirmar(cliente, poliza["id"], usuario, str(uuid4()), cuerpo)
    assert obsoleta.status_code == 409
    assert obsoleta.get_json() == {
        "error": "version_desactualizada",
        "versionActual": 2,
        "correlationId": obsoleta.headers["X-Correlation-ID"],
    }

    assert _contar(url, VersionPolizaModel) == 2
    assert _contar(url, EndosoModel) == 1
    with Session(create_engine(url)) as sesion:
        poliza_db = sesion.scalars(select(PolizaModel)).one()
        assert poliza_db.fecha_fin.isoformat() == poliza["fechaFin"]
        auditoria = sesion.scalars(select(AuditoriaPolizaModel)).all()
    assert sorted((a.resultado, str(a.detalle.get("error"))) for a in auditoria) == [
        ("confirmada", "None"),
        ("rechazada", "IdempotenciaConflictiva"),
        ("rechazada", "VersionDesactualizada"),
    ]


def test_impacto_vencido_o_poliza_no_modificable_no_aplica_cambios_y_se_audita(
    app: Flask, url: str, reloj: Reloj
) -> None:
    cliente = app.test_client()
    usuario = str(uuid4())
    poliza = _emitir(cliente, usuario, "life")
    inicio = date.fromisoformat(poliza["fechaInicio"])
    reloj.dia(inicio + timedelta(days=1))
    cambio = {"versionBase": 1, "plan": "integral", "coberturasOpcionales": ["incapacidad_total"]}
    impacto = _simular(cliente, poliza["id"], usuario, cambio).get_json()
    cuerpo = {**cambio, "confirma": True, "impactoHash": impacto["impactoHash"]}

    reloj.dia(inicio + timedelta(days=2))
    cambiado = _confirmar(cliente, poliza["id"], usuario, str(uuid4()), cuerpo)
    assert cambiado.status_code == 409 and cambiado.get_json()["error"] == "impacto_cambio"

    reloj.dia(date.fromisoformat(poliza["fechaFin"]) + timedelta(days=1))
    vencida = _confirmar(cliente, poliza["id"], usuario, str(uuid4()), cuerpo)
    assert vencida.status_code == 422
    assert vencida.get_json()["motivo"] == "poliza_vencida"

    assert _contar(url, VersionPolizaModel) == 0
    assert _contar(url, EndosoModel) == 0
    with Session(create_engine(url)) as sesion:
        rechazos = sesion.scalars(select(AuditoriaPolizaModel)).all()
    assert sorted((a.detalle["error"], str(a.motivo)) for a in rechazos) == [
        ("ImpactoCambio", "None"),
        ("PolizaNoModificable", "poliza_vencida"),
    ]


def test_renovacion_conserva_condiciones_modificadas(app: Flask, reloj: Reloj) -> None:
    cliente = app.test_client()
    usuario = str(uuid4())
    poliza = _emitir(cliente, usuario)
    reloj.dia(date.fromisoformat(poliza["fechaInicio"]) + timedelta(days=1))
    impacto = _simular(cliente, poliza["id"], usuario, PREMIUM_SIN_ASISTENCIA).get_json()
    cuerpo = {**PREMIUM_SIN_ASISTENCIA, "confirma": True, "impactoHash": impacto["impactoHash"]}
    assert _confirmar(cliente, poliza["id"], usuario, str(uuid4()), cuerpo).status_code == 201

    reloj.dia(date.fromisoformat(poliza["fechaFin"]) - timedelta(days=1))
    oferta = cliente.get(
        f"/v1/polizas/{poliza['id']}/renovacion", headers=_cabeceras(usuario)
    ).get_json()["oferta"]
    assert oferta["prima"]["monto"] == "100100"
    assert [c["nombre"] for c in oferta["coberturas"]] == [
        "Daños al vehículo",
        "Responsabilidad civil",
    ]


def test_confirmaciones_concurrentes_crean_una_sola_version(
    app: Flask, url: str, reloj: Reloj
) -> None:
    usuario = str(uuid4())
    poliza = _emitir(app.test_client(), usuario)
    reloj.dia(date.fromisoformat(poliza["fechaInicio"]) + timedelta(days=1))
    impacto = _simular(app.test_client(), poliza["id"], usuario, PREMIUM_SIN_ASISTENCIA).get_json()
    cuerpo = {**PREMIUM_SIN_ASISTENCIA, "confirma": True, "impactoHash": impacto["impactoHash"]}

    def intentar(_: int) -> int:
        respuesta = _confirmar(app.test_client(), poliza["id"], usuario, str(uuid4()), cuerpo)
        return int(respuesta.status_code)

    with ThreadPoolExecutor(max_workers=4) as executor:
        codigos = list(executor.map(intentar, range(4)))
    assert codigos.count(201) == 1
    assert set(codigos) <= {201, 409}
    assert _contar(url, VersionPolizaModel) == 2
    assert _contar(url, EndosoModel) == 1
