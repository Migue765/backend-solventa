from decimal import Decimal
from uuid import uuid4

from cotizacion.domain.cotizacion import CoberturaCotizada
from cotizacion.domain.dinero import Dinero
from cotizacion.infrastructure.tarificador_tasa_fija import TarificadorTasaFija


def test_prima_es_tasa_sobre_suma_asegurada_menos_deducible():
    cobertura = CoberturaCotizada(uuid4(), Dinero(Decimal("1000000"), "COP"), Dinero(Decimal("100000"), "COP"))

    prima = TarificadorTasaFija(Decimal("0.015")).calcular_prima(uuid4(), cobertura)

    assert prima == Dinero(Decimal("13500"), "COP")


def test_prima_redondea_a_centavos():
    cobertura = CoberturaCotizada(uuid4(), Dinero(Decimal("333"), "USD"), Dinero.cero("USD"))

    prima = TarificadorTasaFija(Decimal("0.0101")).calcular_prima(uuid4(), cobertura)

    assert prima == Dinero(Decimal("3.36"), "USD")
