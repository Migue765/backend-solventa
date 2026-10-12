from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from cotizacion.domain.tarifa import ReglaTarifa

DESDE = datetime(2026, 1, 1, tzinfo=timezone.utc)
HASTA = datetime(2026, 7, 1, tzinfo=timezone.utc)


def regla(hasta=None):
    return ReglaTarifa(uuid4(), "v1", uuid4(), Decimal("0.01"), Decimal("0"), DESDE, hasta)


def test_vigente_desde_su_fecha_de_inicio_sin_fecha_de_fin():
    assert regla().vigente_en(DESDE)
    assert regla().vigente_en(datetime(2030, 1, 1, tzinfo=timezone.utc))


def test_no_vigente_antes_de_su_inicio():
    assert not regla().vigente_en(datetime(2025, 12, 31, tzinfo=timezone.utc))


def test_la_fecha_de_fin_es_exclusiva():
    assert regla(HASTA).vigente_en(datetime(2026, 6, 30, tzinfo=timezone.utc))
    assert not regla(HASTA).vigente_en(HASTA)
