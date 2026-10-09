from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from cotizacion.api.schemas import CrearCotizacionRequest, cotizacion_a_json
from cotizacion.domain.cotizacion import CoberturaCotizada, Cotizacion
from cotizacion.domain.dinero import Dinero


def cuerpo(**cambios):
    datos = {
        "clienteId": str(uuid4()),
        "productoId": str(uuid4()),
        "moneda": "COP",
        "coberturas": [{"coberturaId": str(uuid4()), "sumaAsegurada": "1000"}],
    }
    datos.update(cambios)
    return datos


def test_convierte_solicitud_en_comando():
    datos = cuerpo()

    comando = CrearCotizacionRequest.model_validate(datos).a_comando()

    assert str(comando.cliente_id) == datos["clienteId"]
    assert comando.moneda == "COP"
    assert comando.coberturas[0].suma_asegurada == Decimal("1000")
    assert comando.coberturas[0].deducible == Decimal("0")


@pytest.mark.parametrize(
    "cambios",
    [
        {"clienteId": "no-es-uuid"},
        {"moneda": "pesos"},
        {"coberturas": []},
        {"coberturas": [{"coberturaId": str(uuid4()), "sumaAsegurada": "0"}]},
        {"coberturas": [{"coberturaId": str(uuid4()), "sumaAsegurada": "10", "deducible": "-1"}]},
        {"campoDesconocido": 1},
    ],
)
def test_rechaza_solicitudes_invalidas(cambios):
    with pytest.raises(ValidationError):
        CrearCotizacionRequest.model_validate(cuerpo(**cambios))


def test_serializa_cotizacion_en_camel_case_y_montos_como_texto():
    cobertura = CoberturaCotizada(uuid4(), Dinero(Decimal("1000"), "COP"), Dinero.cero("COP"))
    cotizacion = Cotizacion(cliente_id=uuid4(), producto_id=uuid4(), coberturas=[cobertura])
    ahora = datetime(2026, 10, 1, tzinfo=timezone.utc)
    cotizacion.calcular({cobertura.cobertura_id: Dinero(Decimal("15"), "COP")}, ahora, timedelta(days=30))

    json = cotizacion_a_json(cotizacion)

    assert json["cotizacionId"] == str(cotizacion.cotizacion_id)
    assert json["estado"] == "CALCULADA"
    assert json["primaEstimada"] == {"monto": "15.00", "moneda": "COP"}
    assert json["validaHasta"] == "2026-10-31T00:00:00+00:00"
    assert json["coberturas"][0]["sumaAsegurada"] == {"monto": "1000.00", "moneda": "COP"}
