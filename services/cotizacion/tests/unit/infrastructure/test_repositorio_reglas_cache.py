from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from cotizacion.domain.tarifa import ReglaTarifa
from cotizacion.infrastructure.persistence.repositorio_reglas import ReglasConCache

AHORA = datetime(2026, 10, 1, tzinfo=timezone.utc)
PRODUCTO = uuid4()


class OrigenFalso:
    def __init__(self, regla):
        self.regla = regla
        self.consultas = 0

    def obtener_vigente(self, producto_id, ahora):
        self.consultas += 1
        return self.regla


class Reloj:
    def __init__(self):
        self.segundos = 0.0

    def __call__(self):
        return self.segundos


def una_regla(hasta=None):
    return ReglaTarifa(
        uuid4(),
        "v1",
        PRODUCTO,
        Decimal("0.01"),
        Decimal("0"),
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        hasta,
    )


def test_reutiliza_la_regla_dentro_del_ttl():
    origen, reloj = OrigenFalso(una_regla()), Reloj()
    cache = ReglasConCache(origen, 30, reloj)

    cache.obtener_vigente(PRODUCTO, AHORA)
    reloj.segundos = 29
    cache.obtener_vigente(PRODUCTO, AHORA)

    assert origen.consultas == 1


def test_consulta_de_nuevo_al_vencer_el_ttl():
    origen, reloj = OrigenFalso(una_regla()), Reloj()
    cache = ReglasConCache(origen, 30, reloj)

    cache.obtener_vigente(PRODUCTO, AHORA)
    reloj.segundos = 31
    cache.obtener_vigente(PRODUCTO, AHORA)

    assert origen.consultas == 2


def test_nunca_usa_una_regla_que_dejo_de_estar_vigente():
    origen, reloj = (
        OrigenFalso(una_regla(hasta=datetime(2026, 10, 2, tzinfo=timezone.utc))),
        Reloj(),
    )
    cache = ReglasConCache(origen, 3600, reloj)
    cache.obtener_vigente(PRODUCTO, AHORA)
    origen.regla = None

    resultado = cache.obtener_vigente(PRODUCTO, datetime(2026, 10, 3, tzinfo=timezone.utc))

    assert resultado is None
    assert origen.consultas == 2
