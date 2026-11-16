from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from flask import Flask
from flask.testing import FlaskClient
from polizas.app import create_app
from polizas.infrastructure.models import (
    AuditoriaPolizaModel,
    CancelacionModel,
    PagoRenovacionModel,
    PolizaModel,
)
from polizas.infrastructure.renovaciones import ResultadoPago, SimuladorPagoRenovacion
from sqlalchemy import create_engine, func, select, update
from sqlalchemy.orm import Session, sessionmaker

TOKEN = "polizas-test-internal"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


class Reloj:
    def __init__(self) -> None:
        self.ahora = datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.ahora

    def dia(self, dia: date) -> None:
        self.ahora = datetime.combine(dia, time(15), UTC)


class PasarelaIntermitente:
    def __init__(self, simulador: SimuladorPagoRenovacion) -> None:
        self.simulador = simulador
        self.fallas = 0

    def cobrar(self, renovacion_id: UUID, monto: str, moneda: str) -> ResultadoPago:
        if self.fallas:
            self.fallas -= 1
            raise TimeoutError("respuesta perdida")
        return self.simulador.cobrar(renovacion_id, monto, moneda)


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
def pasarela(url: str) -> PasarelaIntermitente:
    return PasarelaIntermitente(
        SimuladorPagoRenovacion(sessionmaker(create_engine(url)), "confirmado")
    )


@pytest.fixture
def app(url: str, reloj: Reloj, pasarela: PasarelaIntermitente) -> Flask:
    return create_app(url, TOKEN, pasarela=pasarela, reloj=reloj)


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
    return cliente.get(f"/v1/polizas/{poliza_id}/cancelacion", headers=_cabeceras(cliente_id))


def _cancelar(
    cliente: FlaskClient, poliza_id: str, cliente_id: str, clave: str, cuerpo: object
) -> Any:
    return cliente.post(
        f"/v1/polizas/{poliza_id}/cancelaciones", json=cuerpo, headers=_cabeceras(cliente_id, clave)
    )


def _cuerpo(liquidacion_hash: str, motivo: str = "costo") -> dict[str, object]:
    return {"confirma": True, "motivoCancelacion": motivo, "liquidacionHash": liquidacion_hash}


def _contar(url: str, modelo: type[Any]) -> int:
    with Session(create_engine(url)) as sesion:
        return int(sesion.scalar(select(func.count()).select_from(modelo)) or 0)


def _auditoria(url: str) -> list[AuditoriaPolizaModel]:
    with Session(create_engine(url)) as sesion:
        return list(
            sesion.scalars(
                select(AuditoriaPolizaModel)
                .where(AuditoriaPolizaModel.accion == "cancelacion")
                .order_by(AuditoriaPolizaModel.ocurrido_en, AuditoriaPolizaModel.resultado)
            )
        )


def _proporcional(prima: int, no_usados: int, periodo: int) -> str:
    monto = (Decimal(prima) * no_usados / periodo).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return str(int(monto))


def test_consulta_elegibilidad_y_liquidacion(app: Flask, reloj: Reloj) -> None:
    cliente = app.test_client()
    usuario = str(uuid4())
    poliza = _emitir(cliente, usuario)
    inicio, fin = date.fromisoformat(poliza["fechaInicio"]), date.fromisoformat(poliza["fechaFin"])
    hoy = inicio + timedelta(days=10)
    reloj.dia(hoy)

    respuesta = _consultar(cliente, poliza["id"], usuario)

    assert respuesta.status_code == 200
    assert respuesta.headers["Cache-Control"] == "no-store"
    estado = respuesta.get_json()
    assert estado["cancelable"] is True and estado["motivo"] is None
    assert estado["estado"] == "activa" and estado["cancelacion"] is None
    liquidacion = estado["liquidacion"]
    periodo, no_usados = (fin - inicio).days + 1, (fin - hoy).days
    assert liquidacion["fechaEfectiva"] == hoy.isoformat()
    assert liquidacion["finCobertura"] == hoy.isoformat()
    assert [c["codigo"] for c in liquidacion["coberturasFinalizadas"]] == [
        "danos_vehiculo",
        "responsabilidad_civil",
        "asistencia_carretera",
    ]
    assert liquidacion["primaMensual"] == {"monto": "85000", "moneda": "COP"}
    assert liquidacion["diasPeriodo"] == periodo
    assert liquidacion["diasNoUsados"] == no_usados
    devolucion = _proporcional(85000, no_usados, periodo)
    assert liquidacion["devolucion"] == {"monto": devolucion, "moneda": "COP"}
    assert liquidacion["devolucionPeriodosAnulados"]["monto"] == "0"
    assert liquidacion["periodosAnulados"] == []
    assert liquidacion["valorPendiente"] == {"monto": "0", "moneda": "COP"}
    assert len(liquidacion["liquidacionHash"]) == 64


@pytest.mark.parametrize(
    ("desfase", "motivo"),
    [(-1, "poliza_programada"), (None, "fin_de_vigencia"), (1, "poliza_vencida")],
)
def test_vigencia_determina_si_se_puede_cancelar(
    app: Flask, reloj: Reloj, desfase: int | None, motivo: str
) -> None:
    cliente = app.test_client()
    usuario = str(uuid4())
    poliza = _emitir(cliente, usuario)
    inicio, fin = date.fromisoformat(poliza["fechaInicio"]), date.fromisoformat(poliza["fechaFin"])
    reloj.dia(fin if desfase is None else (inicio if desfase < 0 else fin) + timedelta(desfase))

    estado = _consultar(cliente, poliza["id"], usuario).get_json()
    rechazo = _cancelar(cliente, poliza["id"], usuario, str(uuid4()), _cuerpo("a" * 64))

    assert estado["cancelable"] is False and estado["motivo"] == motivo
    assert estado["liquidacion"] is None
    assert rechazo.status_code == 422
    assert rechazo.get_json()["error"] == "poliza_no_cancelable"
    assert rechazo.get_json()["motivo"] == motivo


def test_producto_por_fechas_y_poliza_ajena(app: Flask, url: str) -> None:
    cliente = app.test_client()
    usuario = str(uuid4())
    viaje = _emitir(cliente, usuario, "travel")
    poliza = _emitir(cliente, usuario)

    assert _consultar(cliente, viaje["id"], usuario).get_json()["motivo"] == (
        "producto_no_cancelable"
    )
    ajena = _consultar(cliente, poliza["id"], str(uuid4()))
    assert ajena.status_code == 404
    assert ajena.get_json()["error"] == "poliza_no_encontrada"
    intento = _cancelar(cliente, poliza["id"], str(uuid4()), str(uuid4()), _cuerpo("a" * 64))
    assert intento.status_code == 404
    assert _auditoria(url) == []


def test_requiere_confirmacion_explicita_y_liquidacion_vigente(
    app: Flask, url: str, reloj: Reloj
) -> None:
    cliente = app.test_client()
    usuario = str(uuid4())
    poliza = _emitir(cliente, usuario)
    reloj.dia(date.fromisoformat(poliza["fechaInicio"]) + timedelta(days=3))
    liquidacion = _consultar(cliente, poliza["id"], usuario).get_json()["liquidacion"]
    cuerpo = _cuerpo(liquidacion["liquidacionHash"])

    for invalido in (
        {**cuerpo, "confirma": False},
        {**cuerpo, "confirma": "true"},
        {key: value for key, value in cuerpo.items() if key != "confirma"},
        {**cuerpo, "motivoCancelacion": "porque_si"},
        {**cuerpo, "extra": 1},
    ):
        respuesta = _cancelar(cliente, poliza["id"], usuario, str(uuid4()), invalido)
        assert respuesta.status_code == 400
    sin_clave = cliente.post(
        f"/v1/polizas/{poliza['id']}/cancelaciones", json=cuerpo, headers=_cabeceras(usuario)
    )
    assert sin_clave.status_code == 400

    reloj.dia(date.fromisoformat(poliza["fechaInicio"]) + timedelta(days=4))
    cambio = _cancelar(cliente, poliza["id"], usuario, str(uuid4()), cuerpo)

    assert cambio.status_code == 409
    assert cambio.get_json()["error"] == "liquidacion_cambio"
    assert _contar(url, CancelacionModel) == 0
    assert _consultar(cliente, poliza["id"], usuario).get_json()["estado"] == "activa"
    [rechazo] = _auditoria(url)
    assert rechazo.resultado == "rechazada" and rechazo.motivo == "liquidacion_cambio"


def test_cancelacion_es_definitiva_idempotente_y_auditada(
    app: Flask, url: str, reloj: Reloj
) -> None:
    cliente = app.test_client()
    usuario = str(uuid4())
    poliza = _emitir(cliente, usuario)
    fin = date.fromisoformat(poliza["fechaFin"])
    reloj.dia(fin - timedelta(days=5))
    liquidacion = _consultar(cliente, poliza["id"], usuario).get_json()["liquidacion"]
    cuerpo, clave = _cuerpo(liquidacion["liquidacionHash"]), str(uuid4())

    creada = _cancelar(cliente, poliza["id"], usuario, clave, cuerpo)
    repetida = _cancelar(cliente, poliza["id"], usuario, clave, cuerpo)
    otra_clave = _cancelar(cliente, poliza["id"], usuario, str(uuid4()), cuerpo)
    otro_motivo = _cuerpo(liquidacion["liquidacionHash"], "otro")
    conflicto = _cancelar(cliente, poliza["id"], usuario, clave, otro_motivo)

    assert creada.status_code == 201
    resultado = creada.get_json()
    assert resultado["estado"] == "cancelada" and resultado["simulada"] is True
    assert resultado["motivoCancelacion"] == "costo"
    assert resultado["fechaEfectiva"] == (fin - timedelta(days=5)).isoformat()
    assert resultado["liquidacion"] == liquidacion
    assert repetida.status_code == 200 and repetida.get_json() == resultado
    assert otra_clave.status_code == 422 and otra_clave.get_json()["motivo"] == "poliza_cancelada"
    assert conflicto.status_code == 409
    assert conflicto.get_json()["error"] == "idempotencia_conflictiva"
    assert _contar(url, CancelacionModel) == 1

    estado = _consultar(cliente, poliza["id"], usuario).get_json()
    assert estado["estado"] == "cancelada" and estado["motivo"] == "poliza_cancelada"
    assert estado["cancelable"] is False and estado["cancelacion"] == resultado
    assert estado["vigencia"]["fin"] == poliza["fechaFin"]
    listado = cliente.get(f"/v1/polizas?clienteId={usuario}", headers=HEADERS).get_json()
    assert listado["polizas"][0]["estado"] == "cancelada"

    auditoria = _auditoria(url)
    confirmada = [registro for registro in auditoria if registro.resultado == "confirmada"]
    assert len(confirmada) == 1
    assert confirmada[0].cliente_id == UUID(usuario)
    assert confirmada[0].motivo == "costo"
    assert confirmada[0].detalle["cancelacionId"] == resultado["id"]
    assert confirmada[0].ocurrido_en is not None
    assert sorted(r.motivo or "" for r in auditoria if r.resultado == "rechazada") == [
        "idempotencia_conflictiva",
        "poliza_cancelada",
    ]


def test_poliza_cancelada_no_se_renueva_ni_modifica(app: Flask, reloj: Reloj) -> None:
    cliente = app.test_client()
    usuario = str(uuid4())
    poliza = _emitir(cliente, usuario)
    fin = date.fromisoformat(poliza["fechaFin"])
    reloj.dia(fin - timedelta(days=3))
    liquidacion = _consultar(cliente, poliza["id"], usuario).get_json()["liquidacion"]
    oferta = cliente.get(
        f"/v1/polizas/{poliza['id']}/renovacion", headers=_cabeceras(usuario)
    ).get_json()["oferta"]
    cancelada = _cancelar(
        cliente, poliza["id"], usuario, str(uuid4()), _cuerpo(liquidacion["liquidacionHash"])
    )
    assert cancelada.status_code == 201

    renovacion = cliente.get(f"/v1/polizas/{poliza['id']}/renovacion", headers=_cabeceras(usuario))
    renovar = cliente.post(
        f"/v1/polizas/{poliza['id']}/renovaciones",
        json={"aceptaCondiciones": True, "condicionesHash": oferta["condicionesHash"]},
        headers=_cabeceras(usuario, str(uuid4())),
    )
    modificacion = cliente.get(
        f"/v1/polizas/{poliza['id']}/modificacion", headers=_cabeceras(usuario)
    )

    assert renovacion.get_json()["motivo"] == "poliza_cancelada"
    assert renovacion.get_json()["oferta"] is None
    assert renovar.status_code == 422 and renovar.get_json()["motivo"] == "poliza_cancelada"
    assert modificacion.get_json()["motivo"] == "poliza_cancelada"
    assert modificacion.get_json()["opciones"] is None


def test_devuelve_periodos_renovados_que_no_iniciaron(app: Flask, url: str, reloj: Reloj) -> None:
    cliente = app.test_client()
    usuario = str(uuid4())
    poliza = _emitir(cliente, usuario)
    inicio, fin = date.fromisoformat(poliza["fechaInicio"]), date.fromisoformat(poliza["fechaFin"])
    reloj.dia(fin - timedelta(days=2))
    oferta = cliente.get(
        f"/v1/polizas/{poliza['id']}/renovacion", headers=_cabeceras(usuario)
    ).get_json()["oferta"]
    renovada = cliente.post(
        f"/v1/polizas/{poliza['id']}/renovaciones",
        json={"aceptaCondiciones": True, "condicionesHash": oferta["condicionesHash"]},
        headers=_cabeceras(usuario, str(uuid4())),
    )
    assert renovada.get_json()["estado"] == "renovada"

    liquidacion = _consultar(cliente, poliza["id"], usuario).get_json()["liquidacion"]

    periodo = (fin - inicio).days + 1
    proporcional = _proporcional(85000, 2, periodo)
    assert liquidacion["periodoActual"] == {
        "inicio": poliza["fechaInicio"],
        "fin": poliza["fechaFin"],
    }
    assert liquidacion["periodosAnulados"] == [
        {**oferta["nuevaVigencia"], "prima": {"monto": "85000", "moneda": "COP"}}
    ]
    assert liquidacion["devolucionProporcional"]["monto"] == proporcional
    assert liquidacion["devolucion"]["monto"] == str(int(proporcional) + 85000)

    nuevo_inicio = date.fromisoformat(oferta["nuevaVigencia"]["inicio"])
    reloj.dia(nuevo_inicio + timedelta(days=1))
    en_renovado = _consultar(cliente, poliza["id"], usuario).get_json()["liquidacion"]
    assert en_renovado["periodoActual"] == oferta["nuevaVigencia"]
    assert en_renovado["periodosAnulados"] == []
    cancelada = _cancelar(
        cliente, poliza["id"], usuario, str(uuid4()), _cuerpo(en_renovado["liquidacionHash"])
    )
    assert cancelada.status_code == 201
    assert _contar(url, PagoRenovacionModel) == 1


def test_renovacion_pendiente_bloquea_y_no_se_cobra_tras_cancelar(
    app: Flask, url: str, reloj: Reloj, pasarela: PasarelaIntermitente
) -> None:
    cliente = app.test_client()
    usuario = str(uuid4())
    poliza = _emitir(cliente, usuario)
    reloj.dia(date.fromisoformat(poliza["fechaFin"]) - timedelta(days=1))
    oferta = cliente.get(
        f"/v1/polizas/{poliza['id']}/renovacion", headers=_cabeceras(usuario)
    ).get_json()["oferta"]
    pasarela.fallas = 1
    pendiente = cliente.post(
        f"/v1/polizas/{poliza['id']}/renovaciones",
        json={"aceptaCondiciones": True, "condicionesHash": oferta["condicionesHash"]},
        headers=_cabeceras(usuario, str(uuid4())),
    ).get_json()
    assert pendiente["estado"] == "pendiente"

    bloqueada = _consultar(cliente, poliza["id"], usuario).get_json()
    assert bloqueada["motivo"] == "renovacion_en_curso" and bloqueada["liquidacion"] is None

    # Una cancelacion que gana la carrera impide que la conciliacion cobre o extienda.
    with Session(create_engine(url)) as sesion, sesion.begin():
        sesion.execute(
            update(PolizaModel)
            .where(PolizaModel.poliza_id == UUID(poliza["id"]))
            .values(estado="cancelada")
        )
    resuelta = app.extensions["polizas_renovaciones"].resolver(UUID(pendiente["id"]))

    assert resuelta["estado"] == "rechazada" and resuelta["motivo"] == "poliza_cancelada"
    assert _contar(url, PagoRenovacionModel) == 0
    with Session(create_engine(url)) as sesion:
        guardada = sesion.get(PolizaModel, UUID(poliza["id"]))
        assert guardada is not None and guardada.fecha_fin.isoformat() == poliza["fechaFin"]


def test_cancelaciones_concurrentes_registran_una_sola(app: Flask, url: str, reloj: Reloj) -> None:
    usuario = str(uuid4())
    poliza = _emitir(app.test_client(), usuario)
    reloj.dia(date.fromisoformat(poliza["fechaInicio"]) + timedelta(days=1))
    liquidacion = _consultar(app.test_client(), poliza["id"], usuario).get_json()["liquidacion"]
    cuerpo = _cuerpo(liquidacion["liquidacionHash"])

    def intentar(_: int) -> int:
        respuesta = _cancelar(app.test_client(), poliza["id"], usuario, str(uuid4()), cuerpo)
        return int(respuesta.status_code)

    with ThreadPoolExecutor(max_workers=4) as executor:
        codigos = list(executor.map(intentar, range(4)))
    assert codigos.count(201) == 1
    assert set(codigos) <= {201, 422}
    assert _contar(url, CancelacionModel) == 1
