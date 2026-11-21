from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from cotizacion.app import create_app
from cotizacion.config import Config
from cotizacion.infrastructure.persistence.models import CotizacionModel, PagoSimuladoModel
from sqlalchemy import create_engine, update
from sqlalchemy.orm import Session

from .test_pagos_simulados_api import pagar, preparar

TOKEN = "cotizacion-test-internal"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def cliente_reportes(database_url):
    return create_app(Config(database_url=database_url, internal_api_token=TOKEN)).test_client()


def _corte(valor=None):
    return (valor or datetime.now(UTC)).isoformat().replace("+00:00", "Z")


def _pago(cliente, producto="automotive"):
    base, query, _, _ = preparar(cliente, producto_id=producto)
    respuesta = pagar(cliente, base, query, json={})
    assert respuesta.status_code == 200
    return respuesta.get_json()


def test_primas_por_corte_sin_datos_del_cliente(cliente_reportes, database_url):
    pago = _pago(cliente_reportes, "travel")
    corte = _corte()
    assert cliente_reportes.get(
        "/v1/reportes/primas", query_string={"corte": corte}
    ).status_code == (401)
    respuesta = cliente_reportes.get(
        "/v1/reportes/primas", query_string={"corte": corte}, headers=HEADERS
    )
    _pago(cliente_reportes, "automotive")

    assert respuesta.status_code == 200
    assert respuesta.headers["Cache-Control"] == "no-store"
    cuerpo = respuesta.get_json()
    assert cuerpo["fuente"] == "primas"
    assert cuerpo["registros"] == 1
    with Session(create_engine(database_url)) as sesion:
        modelo = sesion.get(PagoSimuladoModel, UUID(pago["id"]))
        assert modelo is not None
        cliente_id = str(modelo.cliente_id)
        monto = str(Decimal(modelo.monto).quantize(Decimal("0.01")))
        moneda = modelo.moneda
    assert cuerpo["primas"] == [
        {
            "pagoId": pago["id"],
            "producto": "travel",
            "monto": monto,
            "moneda": moneda,
            "confirmadaEn": cuerpo["primas"][0]["confirmadaEn"],
        }
    ]
    assert cliente_id not in respuesta.get_data(as_text=True)
    repetida = cliente_reportes.get(
        "/v1/reportes/primas", query_string={"corte": corte}, headers=HEADERS
    )
    assert repetida.get_json() == cuerpo
    actual = cliente_reportes.get(
        "/v1/reportes/primas", query_string={"corte": _corte()}, headers=HEADERS
    ).get_json()
    assert actual["registros"] == 2
    assert actual["huella"] != cuerpo["huella"]


def test_primas_previas_al_corte(cliente_reportes):
    _pago(cliente_reportes)
    previo = _corte(datetime.now(UTC) - timedelta(days=1))
    respuesta = cliente_reportes.get(
        "/v1/reportes/primas", query_string={"corte": previo}, headers=HEADERS
    )
    assert respuesta.get_json()["registros"] == 0


@pytest.mark.parametrize(
    "query",
    [
        "",
        "?corte=2026-01-01T00:00:00",
        "?corte=x",
        "?corte=2999-01-01T00:00:00Z",
        "?corte=2026-01-01T00:00:00Z&corte=2026-01-02T00:00:00Z",
        "?corte=2026-01-01T00:00:00Z&clienteId=x",
    ],
)
def test_primas_rechaza_corte_invalido(cliente_reportes, query):
    respuesta = cliente_reportes.get("/v1/reportes/primas" + query, headers=HEADERS)
    assert respuesta.status_code == 400


def test_sin_token_configurado_no_expone_primas(client):
    respuesta = client.get("/v1/reportes/primas", query_string={"corte": _corte()}, headers=HEADERS)
    assert respuesta.status_code == 401


def test_prima_sin_producto_identificable_falla(cliente_reportes, database_url):
    _pago(cliente_reportes)
    with Session(create_engine(database_url)) as sesion:
        sesion.execute(
            update(CotizacionModel).values(producto_id=UUID("00000000-0000-4000-8000-000000000000"))
        )
        sesion.commit()
    respuesta = cliente_reportes.get(
        "/v1/reportes/primas", query_string={"corte": _corte()}, headers=HEADERS
    )
    assert respuesta.status_code == 503
    assert "primas" not in respuesta.get_json()
