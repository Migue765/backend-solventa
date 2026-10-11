from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from cotizacion.application.crear_cotizacion import CoberturaSolicitada, CrearCotizacion, CrearCotizacionComando
from cotizacion.domain.dinero import Dinero
from cotizacion.domain.cotizacion import EstadoCotizacion
from cotizacion.domain.errors import ReglaDeNegocioError

AHORA = datetime(2026, 10, 1, tzinfo=timezone.utc)


class RepositorioFalso:
    def __init__(self):
        self.guardadas = []

    def guardar(self, cotizacion):
        self.guardadas.append(cotizacion)


class TarificadorFalso:
    def calcular_prima(self, producto_id, cobertura):
        return Dinero(Decimal("100"), cobertura.suma_asegurada.moneda)


@pytest.fixture
def repositorio():
    return RepositorioFalso()


@pytest.fixture
def caso_de_uso(repositorio):
    return CrearCotizacion(repositorio, TarificadorFalso(), timedelta(days=30), reloj=lambda: AHORA)


def comando(*coberturas):
    return CrearCotizacionComando(cliente_id=uuid4(), producto_id=uuid4(), moneda="COP", coberturas=coberturas)


def test_crea_calcula_y_guarda(caso_de_uso, repositorio):
    resultado = caso_de_uso.ejecutar(
        comando(
            CoberturaSolicitada(uuid4(), Decimal("1000"), Decimal("0")),
            CoberturaSolicitada(uuid4(), Decimal("2000"), Decimal("100")),
        )
    )

    assert resultado.estado is EstadoCotizacion.CALCULADA
    assert resultado.prima_estimada == Dinero(Decimal("200"), "COP")
    assert resultado.valida_hasta == AHORA + timedelta(days=30)
    assert repositorio.guardadas == [resultado]


def test_no_guarda_si_se_viola_una_regla(caso_de_uso, repositorio):
    with pytest.raises(ReglaDeNegocioError):
        caso_de_uso.ejecutar(comando(CoberturaSolicitada(uuid4(), Decimal("100"), Decimal("500"))))

    assert repositorio.guardadas == []
