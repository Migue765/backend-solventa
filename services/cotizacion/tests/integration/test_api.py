from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from cotizacion.app import create_app
from cotizacion.config import Config
from cotizacion.infrastructure.persistence.database import crear_sesiones
from cotizacion.infrastructure.persistence.repositorio_cotizaciones import RepositorioCotizacionesSqlAlchemy


@pytest.fixture
def client(database_url):
    app = create_app(Config(database_url=database_url, tasa_base=Decimal("0.01"), vigencia_dias=15))
    return app.test_client()


def solicitud(**cambios):
    cuerpo = {
        "clienteId": str(uuid4()),
        "productoId": str(uuid4()),
        "moneda": "COP",
        "coberturas": [{"coberturaId": str(uuid4()), "sumaAsegurada": "2000000", "deducible": "200000"}],
    }
    cuerpo.update(cambios)
    return cuerpo


def test_ping(client):
    respuesta = client.get("/ping")

    assert respuesta.status_code == 200
    assert respuesta.get_json() == {"status": "ok"}


def test_crear_cotizacion_la_persiste(client, database_url):
    cuerpo = solicitud()

    respuesta = client.post("/v1/cotizaciones", json=cuerpo)

    assert respuesta.status_code == 201
    datos = respuesta.get_json()
    assert datos["clienteId"] == cuerpo["clienteId"]
    assert datos["estado"] == "CALCULADA"
    assert datos["primaEstimada"] == {"monto": "18000.00", "moneda": "COP"}

    guardada = RepositorioCotizacionesSqlAlchemy(crear_sesiones(database_url)).obtener(UUID(datos["cotizacionId"]))
    assert guardada is not None
    assert guardada.prima_estimada.monto == Decimal("18000.00")


def test_cuerpo_que_no_es_json(client):
    respuesta = client.post("/v1/cotizaciones", data="hola", content_type="text/plain")

    assert respuesta.status_code == 400
    assert respuesta.get_json()["error"] == "solicitud_invalida"


def test_solicitud_invalida(client):
    respuesta = client.post("/v1/cotizaciones", json=solicitud(moneda="pesos"))

    assert respuesta.status_code == 400
    assert respuesta.get_json()["error"] == "solicitud_invalida"


def test_regla_de_negocio_violada(client):
    cuerpo = solicitud(coberturas=[{"coberturaId": str(uuid4()), "sumaAsegurada": "100", "deducible": "500"}])

    respuesta = client.post("/v1/cotizaciones", json=cuerpo)

    assert respuesta.status_code == 422
    assert respuesta.get_json()["error"] == "regla_de_negocio"
