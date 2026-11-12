from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from flask import Flask
from flask.testing import FlaskClient
from polizas.app import create_app
from polizas.infrastructure.models import PagoRenovacionModel, PolizaModel, RenovacionModel
from polizas.infrastructure.renovaciones import ResultadoPago, SimuladorPagoRenovacion
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

TOKEN = "polizas-test-internal"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


class PasarelaIntermitente:
    """Pierde las primeras respuestas antes de dejar que el simulador cobre."""

    def __init__(self, simulador: SimuladorPagoRenovacion, fallas: int) -> None:
        self.simulador = simulador
        self.fallas = fallas

    def cobrar(self, renovacion_id: UUID, monto: str, moneda: str) -> ResultadoPago:
        if self.fallas:
            self.fallas -= 1
            raise TimeoutError("respuesta perdida")
        return self.simulador.cobrar(renovacion_id, monto, moneda)


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


def _app(url: str, reloj: Reloj, resultado: str = "confirmado", fallas: int = 0) -> Flask:
    simulador = SimuladorPagoRenovacion(sessionmaker(create_engine(url)), resultado)
    return create_app(url, TOKEN, pasarela=PasarelaIntermitente(simulador, fallas), reloj=reloj)


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


def _consultar(cliente: FlaskClient, poliza_id: str, cliente_id: str) -> Any:
    return cliente.get(
        f"/v1/polizas/{poliza_id}/renovacion",
        headers={**HEADERS, "X-Cliente-ID": cliente_id},
    )


def _renovar(
    cliente: FlaskClient, poliza_id: str, cliente_id: str, clave: str, condiciones_hash: str
) -> Any:
    return cliente.post(
        f"/v1/polizas/{poliza_id}/renovaciones",
        json={"aceptaCondiciones": True, "condicionesHash": condiciones_hash},
        headers={**HEADERS, "X-Cliente-ID": cliente_id, "Idempotency-Key": clave},
    )


def _contar(url: str, modelo: type[Any]) -> int:
    with Session(create_engine(url)) as sesion:
        return int(sesion.scalar(select(func.count()).select_from(modelo)) or 0)


def test_elegibilidad_por_ventana_producto_estado_y_propietario(url: str, reloj: Reloj) -> None:
    cliente = _app(url, reloj).test_client()
    usuario = str(uuid4())
    poliza = _emitir(cliente, usuario)
    fin = date.fromisoformat(poliza["fechaFin"])

    lejos = _consultar(cliente, poliza["id"], usuario).get_json()
    assert lejos["renovable"] is False
    assert lejos["motivo"] == "fuera_de_ventana"
    assert lejos["oferta"] is None
    assert lejos["ventana"] == {
        "desde": (fin - timedelta(days=15)).isoformat(),
        "hasta": fin.isoformat(),
    }

    reloj.dia(fin - timedelta(days=3))
    respuesta = _consultar(cliente, poliza["id"], usuario)
    assert respuesta.status_code == 200
    assert respuesta.headers["Cache-Control"] == "no-store"
    elegible = respuesta.get_json()
    assert elegible["renovable"] is True and elegible["motivo"] is None
    oferta = elegible["oferta"]
    assert oferta["nuevaVigencia"]["inicio"] == (fin + timedelta(days=1)).isoformat()
    assert oferta["prima"] == {"monto": "85000", "moneda": "COP", "periodicidad": "mensual"}
    assert len(oferta["coberturas"]) == 3 and oferta["condiciones"]
    assert oferta["requierePago"] is True and len(oferta["condicionesHash"]) == 64
    assert elegible["ultimoIntento"] is None

    reloj.dia(fin + timedelta(days=1))
    assert _consultar(cliente, poliza["id"], usuario).get_json()["motivo"] == "poliza_vencida"
    reloj.dia(date.fromisoformat(poliza["fechaInicio"]) - timedelta(days=1))
    assert _consultar(cliente, poliza["id"], usuario).get_json()["motivo"] == "poliza_programada"

    viaje = _emitir(cliente, usuario, "travel")
    reloj.dia(date.fromisoformat(viaje["fechaFin"]))
    assert _consultar(cliente, viaje["id"], usuario).get_json()["motivo"] == (
        "producto_no_renovable"
    )

    assert _consultar(cliente, poliza["id"], str(uuid4())).status_code == 404
    assert _consultar(cliente, str(uuid4()), usuario).status_code == 404
    assert cliente.get(f"/v1/polizas/{poliza['id']}/renovacion", headers=HEADERS).status_code == 400
    sin_token = cliente.get(
        f"/v1/polizas/{poliza['id']}/renovacion", headers={"X-Cliente-ID": usuario}
    )
    assert sin_token.status_code == 401


def test_renovacion_exitosa_es_idempotente_y_no_duplica(url: str, reloj: Reloj) -> None:
    cliente = _app(url, reloj).test_client()
    usuario = str(uuid4())
    poliza = _emitir(cliente, usuario)
    fin = date.fromisoformat(poliza["fechaFin"])
    reloj.dia(fin - timedelta(days=2))
    oferta = _consultar(cliente, poliza["id"], usuario).get_json()["oferta"]
    clave = str(uuid4())

    assert (
        _renovar(cliente, poliza["id"], usuario, clave, "0" * 64).get_json()["error"]
        == "condiciones_cambiaron"
    )
    primera = _renovar(cliente, poliza["id"], usuario, clave, oferta["condicionesHash"])
    assert primera.status_code == 201
    renovacion = primera.get_json()
    assert renovacion["estado"] == "renovada" and renovacion["motivo"] is None
    assert renovacion["nuevaVigencia"] == oferta["nuevaVigencia"]
    assert renovacion["prima"] == oferta["prima"] and renovacion["simulada"] is True

    replay = _renovar(cliente, poliza["id"], usuario, clave, oferta["condicionesHash"])
    assert replay.status_code == 200 and replay.get_json() == renovacion
    otra_poliza = _emitir(cliente, usuario)
    reutilizada = _renovar(cliente, otra_poliza["id"], usuario, clave, oferta["condicionesHash"])
    assert reutilizada.status_code == 409
    assert reutilizada.get_json()["error"] == "idempotencia_conflictiva"

    listadas = cliente.get("/v1/polizas", query_string={"clienteId": usuario}, headers=HEADERS)
    renovada = next(p for p in listadas.get_json()["polizas"] if p["id"] == poliza["id"])
    assert renovada["fechaInicio"] == poliza["fechaInicio"]
    assert renovada["fechaFin"] == oferta["nuevaVigencia"]["fin"]
    assert renovada["estado"] == "vigente"

    segunda = _renovar(cliente, poliza["id"], usuario, str(uuid4()), oferta["condicionesHash"])
    assert segunda.status_code == 422
    assert segunda.get_json() == {
        "error": "poliza_no_renovable",
        "motivo": "fuera_de_ventana",
        "correlationId": segunda.headers["X-Correlation-ID"],
    }
    assert _contar(url, PagoRenovacionModel) == 1
    assert _contar(url, RenovacionModel) == 1
    assert _contar(url, PolizaModel) == 2
    ultimo = _consultar(cliente, poliza["id"], usuario).get_json()["ultimoIntento"]
    assert ultimo == renovacion


def test_aceptacion_explicita_y_contrato_de_solicitud(url: str, reloj: Reloj) -> None:
    cliente = _app(url, reloj).test_client()
    usuario = str(uuid4())
    poliza = _emitir(cliente, usuario)
    reloj.dia(date.fromisoformat(poliza["fechaFin"]))
    condiciones_hash = _consultar(cliente, poliza["id"], usuario).get_json()["oferta"][
        "condicionesHash"
    ]
    ruta = f"/v1/polizas/{poliza['id']}/renovaciones"
    cabeceras = {**HEADERS, "X-Cliente-ID": usuario, "Idempotency-Key": str(uuid4())}
    for cuerpo in (
        {"aceptaCondiciones": False, "condicionesHash": condiciones_hash},
        {"aceptaCondiciones": "true", "condicionesHash": condiciones_hash},
        {"condicionesHash": condiciones_hash},
        {"aceptaCondiciones": True, "condicionesHash": condiciones_hash, "extra": 1},
    ):
        assert cliente.post(ruta, json=cuerpo, headers=cabeceras).status_code == 400
    sin_clave = {**HEADERS, "X-Cliente-ID": usuario}
    cuerpo_valido = {"aceptaCondiciones": True, "condicionesHash": condiciones_hash}
    assert cliente.post(ruta, json=cuerpo_valido, headers=sin_clave).status_code == 400
    assert _contar(url, RenovacionModel) == 0


def test_pago_rechazado_conserva_poliza_y_registra_intento(url: str, reloj: Reloj) -> None:
    cliente = _app(url, reloj, resultado="rechazado").test_client()
    usuario = str(uuid4())
    poliza = _emitir(cliente, usuario)
    reloj.dia(date.fromisoformat(poliza["fechaFin"]) - timedelta(days=1))
    oferta = _consultar(cliente, poliza["id"], usuario).get_json()["oferta"]

    rechazada = _renovar(cliente, poliza["id"], usuario, str(uuid4()), oferta["condicionesHash"])
    assert rechazada.status_code == 201
    assert rechazada.get_json()["estado"] == "rechazada"
    assert rechazada.get_json()["motivo"] == "pago_rechazado"
    estado = _consultar(cliente, poliza["id"], usuario).get_json()
    assert estado["vigenciaActual"]["fin"] == poliza["fechaFin"]
    assert estado["renovable"] is True
    assert estado["ultimoIntento"]["estado"] == "rechazada"

    nuevo_intento = _renovar(
        cliente, poliza["id"], usuario, str(uuid4()), oferta["condicionesHash"]
    )
    assert nuevo_intento.status_code == 201
    assert _contar(url, RenovacionModel) == 2
    assert _contar(url, PagoRenovacionModel) == 2


def test_pago_ambiguo_queda_pendiente_y_se_concilia_sin_doble_cobro(url: str, reloj: Reloj) -> None:
    app = _app(url, reloj, fallas=1)
    cliente = app.test_client()
    usuario = str(uuid4())
    poliza = _emitir(cliente, usuario)
    reloj.dia(date.fromisoformat(poliza["fechaFin"]))
    oferta = _consultar(cliente, poliza["id"], usuario).get_json()["oferta"]
    clave = str(uuid4())

    pendiente = _renovar(cliente, poliza["id"], usuario, clave, oferta["condicionesHash"])
    assert pendiente.status_code == 202
    assert pendiente.get_json()["estado"] == "pendiente"
    assert (
        _consultar(cliente, poliza["id"], usuario).get_json()["vigenciaActual"]["fin"]
        == (poliza["fechaFin"])
    )
    competidora = _renovar(cliente, poliza["id"], usuario, str(uuid4()), oferta["condicionesHash"])
    assert competidora.status_code == 409
    assert competidora.get_json()["error"] == "renovacion_en_curso"
    assert competidora.get_json()["renovacionId"] == pendiente.get_json()["id"]

    recuperada = _renovar(cliente, poliza["id"], usuario, clave, oferta["condicionesHash"])
    assert recuperada.status_code == 200
    assert recuperada.get_json()["estado"] == "renovada"
    assert recuperada.get_json()["id"] == pendiente.get_json()["id"]
    assert _contar(url, PagoRenovacionModel) == 1


def test_conciliador_completa_renovaciones_pendientes(url: str, reloj: Reloj) -> None:
    app = _app(url, reloj, fallas=1)
    cliente = app.test_client()
    usuario = str(uuid4())
    poliza = _emitir(cliente, usuario)
    reloj.dia(date.fromisoformat(poliza["fechaFin"]))
    oferta = _consultar(cliente, poliza["id"], usuario).get_json()["oferta"]
    pendiente = _renovar(cliente, poliza["id"], usuario, str(uuid4()), oferta["condicionesHash"])
    assert pendiente.status_code == 202

    resultado = app.test_cli_runner().invoke(args=["conciliar-renovaciones"])
    assert "renovaciones_conciliadas=0" in resultado.output
    renovaciones = app.extensions["polizas_renovaciones"]
    assert renovaciones.conciliar_pendientes(timedelta(0)) == 1
    estado = _consultar(cliente, poliza["id"], usuario).get_json()
    assert estado["ultimoIntento"]["estado"] == "renovada"
    assert estado["vigenciaActual"]["fin"] == oferta["nuevaVigencia"]["fin"]


def test_renovaciones_concurrentes_solo_extienden_un_periodo(url: str, reloj: Reloj) -> None:
    app = _app(url, reloj)
    usuario = str(uuid4())
    poliza = _emitir(app.test_client(), usuario)
    reloj.dia(date.fromisoformat(poliza["fechaFin"]))
    oferta = _consultar(app.test_client(), poliza["id"], usuario).get_json()["oferta"]

    def intentar(_: int) -> int:
        respuesta = _renovar(
            app.test_client(), poliza["id"], usuario, str(uuid4()), oferta["condicionesHash"]
        )
        return int(respuesta.status_code)

    with ThreadPoolExecutor(max_workers=4) as executor:
        codigos = list(executor.map(intentar, range(4)))
    assert codigos.count(201) == 1
    assert set(codigos) <= {201, 409, 422}
    assert _contar(url, PagoRenovacionModel) == 1
    actual = _consultar(app.test_client(), poliza["id"], usuario).get_json()
    assert actual["vigenciaActual"]["fin"] == oferta["nuevaVigencia"]["fin"]
