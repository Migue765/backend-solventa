from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from cotizacion.domain.cotizacion import CoberturaCotizada
from cotizacion.domain.dinero import Dinero
from cotizacion.domain.tarifa import PerfilRiesgo, ReglaTarifa
from cotizacion.infrastructure.tarificador_por_regla import TarificadorPorRegla


def regla(tasa="0.015", factor="0"):
    return ReglaTarifa(
        regla_id=uuid4(),
        version="v1",
        producto_id=uuid4(),
        tasa=Decimal(tasa),
        factor_perfil=Decimal(factor),
        vigente_desde=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def perfil(score="0"):
    return PerfilRiesgo("p-1", Decimal(score), "m-1")


def cobertura(suma, deducible, moneda="COP"):
    return CoberturaCotizada(
        uuid4(), Dinero(Decimal(suma), moneda), Dinero(Decimal(deducible), moneda)
    )


def test_prima_es_tasa_sobre_suma_asegurada_menos_deducible():
    prima = TarificadorPorRegla().calcular_prima(regla(), perfil(), cobertura("1000000", "100000"))

    assert prima == Dinero(Decimal("13500"), "COP")


def test_el_score_del_perfil_incrementa_la_prima():
    prima = TarificadorPorRegla().calcular_prima(
        regla(factor="0.10"), perfil("0.5"), cobertura("1000000", "100000")
    )

    assert prima == Dinero(Decimal("14175"), "COP")  # 900000 * 0.015 * 1.05


def test_prima_redondea_a_centavos():
    prima = TarificadorPorRegla().calcular_prima(
        regla(tasa="0.0101"), perfil(), cobertura("333", "0", "USD")
    )

    assert prima == Dinero(Decimal("3.36"), "USD")
