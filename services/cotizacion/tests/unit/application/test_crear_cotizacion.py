from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from cotizacion.application.calculadora_cotizacion import CalculadoraCotizacion
from cotizacion.application.crear_cotizacion import (
    CoberturaSolicitada,
    CrearCotizacion,
    CrearCotizacionComando,
)
from cotizacion.domain.cotizacion import EstadoCotizacion
from cotizacion.domain.dinero import Dinero
from cotizacion.domain.errors import ReglaDeNegocioError
from cotizacion.domain.tarifa import PerfilRiesgo, ReglaTarifa

AHORA = datetime(2026, 10, 1, tzinfo=timezone.utc)


class RepositorioFalso:
    def __init__(self):
        self.guardadas = []

    def guardar(self, cotizacion):
        self.guardadas.append(cotizacion)


class ReglasFalsas:
    def __init__(self, regla=None):
        self.regla = regla

    def obtener_vigente(self, producto_id, ahora):
        return self.regla


class PerfilesFalsos:
    def __init__(self, perfil=None):
        self.perfil = perfil

    def obtener(self, perfil_id, cliente_id):
        return self.perfil


class TarificadorFalso:
    def calcular_prima(self, regla, perfil, cobertura):
        return Dinero(Decimal("100"), cobertura.suma_asegurada.moneda)


def una_regla():
    return ReglaTarifa(
        regla_id=uuid4(),
        version="tarifa-test",
        producto_id=uuid4(),
        tasa=Decimal("0.01"),
        factor_perfil=Decimal("0"),
        vigente_desde=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def un_perfil():
    return PerfilRiesgo("perfil-1", Decimal("0.5"), "modelo-test")


def caso_de_uso(repositorio, regla=None, perfil=None):
    calculadora = CalculadoraCotizacion(
        ReglasFalsas(regla), PerfilesFalsos(perfil), TarificadorFalso(), timedelta(days=30)
    )
    return CrearCotizacion(repositorio, calculadora, reloj=lambda: AHORA)


def comando(*coberturas):
    return CrearCotizacionComando(
        cliente_id=uuid4(),
        producto_id=uuid4(),
        perfil_id="perfil-1",
        moneda="COP",
        coberturas=coberturas,
    )


@pytest.fixture
def repositorio():
    return RepositorioFalso()


def test_crea_calcula_y_guarda_con_su_decision(repositorio):
    regla = una_regla()

    resultado = caso_de_uso(repositorio, regla, un_perfil()).ejecutar(
        comando(
            CoberturaSolicitada(uuid4(), Decimal("1000"), Decimal("0")),
            CoberturaSolicitada(uuid4(), Decimal("2000"), Decimal("100")),
        )
    )

    assert resultado.estado is EstadoCotizacion.CALCULADA
    assert resultado.prima_estimada == Dinero(Decimal("200"), "COP")
    assert resultado.valida_hasta == AHORA + timedelta(days=30)
    assert resultado.instantanea.regla_version == "tarifa-test"
    assert resultado.instantanea.perfil_id == "perfil-1"
    assert repositorio.guardadas == [resultado]


def test_no_guarda_si_se_viola_una_regla(repositorio):
    with pytest.raises(ReglaDeNegocioError):
        caso_de_uso(repositorio, una_regla(), un_perfil()).ejecutar(
            comando(CoberturaSolicitada(uuid4(), Decimal("100"), Decimal("500")))
        )

    assert repositorio.guardadas == []


def test_rechaza_si_no_hay_regla_vigente(repositorio):
    with pytest.raises(ReglaDeNegocioError, match="reglas de tarificación vigentes"):
        caso_de_uso(repositorio, None, un_perfil()).ejecutar(
            comando(CoberturaSolicitada(uuid4(), Decimal("1000"), Decimal("0")))
        )

    assert repositorio.guardadas == []


def test_rechaza_si_no_existe_el_perfil(repositorio):
    with pytest.raises(ReglaDeNegocioError, match="perfil de riesgo"):
        caso_de_uso(repositorio, una_regla(), None).ejecutar(
            comando(CoberturaSolicitada(uuid4(), Decimal("1000"), Decimal("0")))
        )

    assert repositorio.guardadas == []


def test_mismas_entradas_y_version_producen_la_misma_decision(repositorio):
    regla, perfil = una_regla(), un_perfil()
    cobertura = CoberturaSolicitada(uuid4(), Decimal("1000"), Decimal("0"))
    cliente, producto = uuid4(), uuid4()

    def calcular():
        return caso_de_uso(repositorio, regla, perfil).ejecutar(
            CrearCotizacionComando(cliente, producto, "perfil-1", "COP", (cobertura,))
        )

    primera, segunda = calcular(), calcular()

    assert primera.prima_estimada == segunda.prima_estimada
    assert primera.instantanea.hash_decision == segunda.instantanea.hash_decision
    assert primera.cotizacion_id != segunda.cotizacion_id
