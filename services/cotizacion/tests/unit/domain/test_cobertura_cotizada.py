from decimal import Decimal
from uuid import uuid4

import pytest

from cotizacion.domain.cotizacion import CoberturaCotizada
from cotizacion.domain.dinero import Dinero
from cotizacion.domain.errors import ReglaDeNegocioError


def cobertura(suma="1000", deducible="0", moneda_deducible="COP"):
    return CoberturaCotizada(
        uuid4(), Dinero(Decimal(suma), "COP"), Dinero(Decimal(deducible), moneda_deducible)
    )


def test_cobertura_valida_sin_prima():
    assert cobertura(deducible="100").prima is None


def test_suma_asegurada_debe_ser_mayor_a_cero():
    with pytest.raises(ReglaDeNegocioError):
        cobertura(suma="0")


def test_deducible_en_otra_moneda():
    with pytest.raises(ReglaDeNegocioError):
        cobertura(moneda_deducible="USD")


def test_deducible_no_puede_superar_suma_asegurada():
    with pytest.raises(ReglaDeNegocioError):
        cobertura(suma="100", deducible="200")
