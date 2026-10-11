from decimal import Decimal

import pytest

from cotizacion.domain.dinero import Dinero
from cotizacion.domain.errors import ReglaDeNegocioError


def test_redondea_a_centavos():
    assert Dinero(Decimal("10.005"), "COP").monto == Decimal("10.01")


def test_no_admite_montos_negativos():
    with pytest.raises(ReglaDeNegocioError):
        Dinero(Decimal("-1"), "COP")


def test_cero():
    assert Dinero.cero("COP") == Dinero(Decimal("0"), "COP")


def test_suma_misma_moneda():
    assert Dinero(Decimal("1.50"), "COP") + Dinero(Decimal("2"), "COP") == Dinero(Decimal("3.50"), "COP")


def test_no_suma_monedas_distintas():
    with pytest.raises(ReglaDeNegocioError):
        Dinero(Decimal("1"), "COP") + Dinero(Decimal("1"), "USD")
