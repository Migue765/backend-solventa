from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from cotizacion.domain.cotizacion import CoberturaCotizada, Cotizacion, EstadoCotizacion
from cotizacion.domain.dinero import Dinero
from cotizacion.domain.errors import ReglaDeNegocioError

AHORA = datetime(2026, 10, 1, tzinfo=timezone.utc)


def cobertura(moneda="COP"):
    return CoberturaCotizada(uuid4(), Dinero(Decimal("1000"), moneda), Dinero.cero(moneda))


def cotizacion(*coberturas):
    return Cotizacion(cliente_id=uuid4(), producto_id=uuid4(), coberturas=list(coberturas))


def test_nueva_cotizacion_queda_pendiente():
    nueva = cotizacion(cobertura())

    assert nueva.estado is EstadoCotizacion.PENDIENTE
    assert nueva.prima_estimada is None
    assert nueva.moneda == "COP"


def test_requiere_coberturas():
    with pytest.raises(ReglaDeNegocioError):
        cotizacion()


def test_no_admite_coberturas_repetidas():
    c = cobertura()
    with pytest.raises(ReglaDeNegocioError):
        cotizacion(c, c)


def test_no_admite_monedas_mezcladas():
    with pytest.raises(ReglaDeNegocioError):
        cotizacion(cobertura(), cobertura(moneda="USD"))


def test_calcular_suma_primas_y_fija_vigencia():
    c1, c2 = cobertura(), cobertura()
    cot = cotizacion(c1, c2)

    cot.calcular(
        {c1.cobertura_id: Dinero(Decimal("100"), "COP"), c2.cobertura_id: Dinero(Decimal("50.5"), "COP")},
        AHORA,
        timedelta(days=30),
    )

    assert cot.estado is EstadoCotizacion.CALCULADA
    assert c1.prima == Dinero(Decimal("100"), "COP")
    assert cot.prima_estimada == Dinero(Decimal("150.50"), "COP")
    assert cot.valida_hasta == AHORA + timedelta(days=30)


def test_no_se_puede_calcular_dos_veces():
    c = cobertura()
    cot = cotizacion(c)
    primas = {c.cobertura_id: Dinero(Decimal("1"), "COP")}
    cot.calcular(primas, AHORA, timedelta(days=1))

    with pytest.raises(ReglaDeNegocioError):
        cot.calcular(primas, AHORA, timedelta(days=1))
