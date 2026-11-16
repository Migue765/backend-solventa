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
def reloj() -> Reloj:
    return Reloj()


@pytest.fixture
def app(tmp_path: Path, reloj: Reloj) -> Flask:
    url = f"sqlite:///{tmp_path / 'polizas.db'}"
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    return create_app(url, TOKEN, reloj=reloj)


def _emitir(cliente: FlaskClient, cliente_id: str, tipo: str = "automotive") -> dict[str, Any]:
    fechas = None
    if tipo in {"travel", "parametric-weather"}:
        hoy = datetime.now(UTC).date()
        fechas = {"inicio": hoy.isoformat(), "fin": (hoy + timedelta(days=10)).isoformat()}
    respuesta = cliente.post(
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
    assert respuesta.status_code == 200
    poliza: dict[str, Any] = respuesta.get_json()
    return poliza


def _cabeceras(cliente_id: str) -> dict[str, str]:
    return {**HEADERS, "X-Cliente-ID": cliente_id}


def test_lista_del_cliente_con_resumen_y_aislamiento(app: Flask) -> None:
    cliente = app.test_client()
    usuario, otro = str(uuid4()), str(uuid4())
    auto = _emitir(cliente, usuario)
    viaje = _emitir(cliente, usuario, "travel")
    _emitir(cliente, otro, "life")

    respuesta = cliente.get("/v1/clientes/me/polizas", headers=_cabeceras(usuario))
    assert respuesta.status_code == 200
    assert respuesta.headers["Cache-Control"] == "no-store"
    polizas = respuesta.get_json()["polizas"]
    assert {p["id"] for p in polizas} == {auto["id"], viaje["id"]}
    resumen = next(p for p in polizas if p["id"] == auto["id"])
    assert set(resumen) == {
        "id",
        "numero",
        "producto",
        "estado",
        "vigencia",
        "coberturaPrincipal",
        "simulada",
    }
    assert resumen["numero"].startswith("POL-") and auto["id"][:8].upper() in resumen["numero"]
    assert resumen["producto"] == {"tipo": "automotive", "nombre": "Seguro Automotriz Total"}
    assert resumen["estado"] == "vigente"
    assert resumen["vigencia"] == {"inicio": auto["fechaInicio"], "fin": auto["fechaFin"]}
    assert resumen["coberturaPrincipal"] == {
        "codigo": "danos_vehiculo",
        "nombre": "Daños al vehículo",
    }
    principal_viaje = next(p for p in polizas if p["id"] == viaje["id"])["coberturaPrincipal"]
    assert principal_viaje["nombre"] == "Asistencia médica"


def test_lista_vacia_y_solicitudes_invalidas(app: Flask) -> None:
    cliente = app.test_client()
    vacia = cliente.get("/v1/clientes/me/polizas", headers=_cabeceras(str(uuid4())))
    assert vacia.status_code == 200 and vacia.get_json() == {"polizas": []}
    assert cliente.get("/v1/clientes/me/polizas", headers=HEADERS).status_code == 400
    assert (
        cliente.get("/v1/clientes/me/polizas?x=1", headers=_cabeceras(str(uuid4()))).status_code
        == 400
    )
    assert cliente.get("/v1/clientes/me/polizas").status_code == 401


def test_detalle_vigente_refleja_modificacion_y_renovacion(app: Flask, reloj: Reloj) -> None:
    cliente = app.test_client()
    usuario = str(uuid4())
    poliza = _emitir(cliente, usuario)
    ruta = f"/v1/clientes/me/polizas/{poliza['id']}"

    inicial = cliente.get(ruta, headers=_cabeceras(usuario))
    assert inicial.status_code == 200
    detalle = inicial.get_json()
    assert detalle["version"] == 1 and detalle["plan"] == "esencial"
    assert detalle["prima"] == {"monto": "85000", "moneda": "COP", "periodicidad": "mensual"}
    assert all(c["incluida"] for c in detalle["coberturas"])
    assert detalle["suscripcionId"] == poliza["suscripcionId"]
    assert detalle["condiciones"] and detalle["simulada"] is True
    assert detalle["estado"] == "vigente"

    reloj.dia(date.fromisoformat(poliza["fechaInicio"]) + timedelta(days=1))
    cambio = {"versionBase": 1, "plan": "premium", "coberturasOpcionales": []}
    impacto = cliente.post(
        f"/v1/polizas/{poliza['id']}/modificaciones/simulacion",
        json=cambio,
        headers=_cabeceras(usuario),
    ).get_json()
    confirmada = cliente.post(
        f"/v1/polizas/{poliza['id']}/modificaciones",
        json={**cambio, "confirma": True, "impactoHash": impacto["impactoHash"]},
        headers={**_cabeceras(usuario), "Idempotency-Key": str(uuid4())},
    )
    assert confirmada.status_code == 201

    actual = cliente.get(ruta, headers=_cabeceras(usuario)).get_json()
    assert actual["version"] == 2 and actual["plan"] == "premium"
    assert actual["prima"]["monto"] == "100100"
    asistencia = next(c for c in actual["coberturas"] if c["codigo"] == "asistencia_carretera")
    assert asistencia["incluida"] is False

    reloj.dia(date.fromisoformat(poliza["fechaFin"]) - timedelta(days=1))
    renovada = cliente.post(
        f"/v1/polizas/{poliza['id']}/renovaciones",
        json={"aceptaCondiciones": True, "condicionesHash": _oferta(cliente, poliza, usuario)},
        headers={**_cabeceras(usuario), "Idempotency-Key": str(uuid4())},
    )
    assert renovada.status_code == 201, renovada.get_json()
    tras_renovar = cliente.get(ruta, headers=_cabeceras(usuario)).get_json()
    assert tras_renovar["vigencia"]["fin"] == renovada.get_json()["nuevaVigencia"]["fin"]

    reloj.dia(date.fromisoformat(tras_renovar["vigencia"]["fin"]) + timedelta(days=1))
    assert cliente.get(ruta, headers=_cabeceras(usuario)).get_json()["estado"] == "vencida"


def _oferta(cliente: FlaskClient, poliza: dict[str, Any], usuario: str) -> str:
    respuesta = cliente.get(f"/v1/polizas/{poliza['id']}/renovacion", headers=_cabeceras(usuario))
    assert respuesta.status_code == 200
    return str(respuesta.get_json()["oferta"]["condicionesHash"])


@pytest.mark.parametrize("tipo", ["travel", "parametric-weather"])
def test_detalle_de_producto_sin_condiciones_modificables(app: Flask, tipo: str) -> None:
    cliente = app.test_client()
    usuario = str(uuid4())
    poliza = _emitir(cliente, usuario, tipo)
    detalle = cliente.get(
        f"/v1/clientes/me/polizas/{poliza['id']}", headers=_cabeceras(usuario)
    ).get_json()
    assert detalle["version"] is None and detalle["plan"] is None and detalle["prima"] is None
    assert detalle["coberturas"][0]["codigo"] == detalle["coberturaPrincipal"]["codigo"]


def test_detalle_ajeno_o_inexistente_no_revela_existencia(app: Flask) -> None:
    cliente = app.test_client()
    duenio = str(uuid4())
    poliza = _emitir(cliente, duenio)
    ajena = cliente.get(f"/v1/clientes/me/polizas/{poliza['id']}", headers=_cabeceras(str(uuid4())))
    inexistente = cliente.get(f"/v1/clientes/me/polizas/{uuid4()}", headers=_cabeceras(duenio))
    assert ajena.status_code == inexistente.status_code == 404
    assert ajena.get_json()["error"] == inexistente.get_json()["error"] == "poliza_no_encontrada"
    assert poliza["id"] not in ajena.get_data(as_text=True)
    assert cliente.get(f"/v1/clientes/me/polizas/{poliza['id']}").status_code == 401
    assert (
        cliente.get(
            f"/v1/clientes/me/polizas/{poliza['id']}?ver=todo", headers=_cabeceras(duenio)
        ).status_code
        == 400
    )


def test_fallo_de_persistencia_no_parece_lista_vacia(tmp_path: Path) -> None:
    app = create_app(f"sqlite:///{tmp_path / 'sin-migrar.db'}", TOKEN)
    respuesta = app.test_client().get("/v1/clientes/me/polizas", headers=_cabeceras(str(uuid4())))
    assert respuesta.status_code == 503
    assert respuesta.get_json()["error"] == "servicio_no_disponible"
