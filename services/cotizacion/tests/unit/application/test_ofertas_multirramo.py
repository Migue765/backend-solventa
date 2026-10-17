from decimal import Decimal

import pytest
from cotizacion.application.ofertas import _plantillas_producto
from cotizacion.domain.errors import ReglaDeNegocioError


@pytest.mark.parametrize(
    ("evento", "monto", "fin", "prima"),
    [
        ("heavy-rain", "10000000", "2030-01-30", "30000"),
        ("drought", "10000000", "2030-01-30", "36000"),
        ("heavy-rain", "20000000", "2030-01-30", "60000"),
        ("heavy-rain", "10000000", "2030-03-01", "60000"),
        ("heavy-rain", "1", "2030-01-01", "1000"),
    ],
)
def test_prima_climatica_depende_del_evento_monto_y_periodo(evento, monto, fin, prima) -> None:
    plantillas = _plantillas_producto(
        "parametric-weather",
        {
            "location": "Medellín, Antioquia",
            "weatherEvent": evento,
            "insuredAmount": monto,
            "startDate": "2030-01-01",
            "endDate": fin,
        },
    )
    assert plantillas[0].prima == Decimal(prima)
    assert len(plantillas) == 3
    monto_formateado = {
        "1": "$1 COP",
        "10000000": "$10.000.000 COP",
        "20000000": "$20.000.000 COP",
    }[monto]
    assert all(monto_formateado in plantilla.cobertura_principal for plantilla in plantillas)


def test_cobertura_predeterminada_conserva_las_ofertas_de_vuelo_anteriores() -> None:
    assert _plantillas_producto("parametric", {}) == _plantillas_producto(
        "parametric", {"coverageType": "delay-cancellation"}
    )


@pytest.mark.parametrize(
    ("producto", "datos"),
    [
        ("desconocido", {}),
        ("parametric", {"coverageType": "desconocida"}),
        ("parametric-weather", {"weatherEvent": "desconocido"}),
    ],
)
def test_rechaza_productos_y_variantes_sin_fallback(producto, datos) -> None:
    with pytest.raises(ReglaDeNegocioError):
        _plantillas_producto(producto, datos)
