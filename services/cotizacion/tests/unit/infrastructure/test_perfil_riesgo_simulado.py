from decimal import Decimal

from cotizacion.domain.perfil_riesgo import NivelRiesgo
from cotizacion.infrastructure.perfil_riesgo_simulado import PerfilRiesgoSimulado


def test_repite_el_perfil_para_la_misma_identificacion() -> None:
    adaptador = PerfilRiesgoSimulado()

    primero = adaptador.consultar("cc", "1234567890")
    segundo = adaptador.consultar("cc", "1234567890")

    assert primero == segundo


def test_genera_los_tres_escenarios_simulados() -> None:
    adaptador = PerfilRiesgoSimulado()

    perfiles = {
        adaptador.consultar("cc", identificacion).nivel
        for identificacion in ("1000000000", "2000000000", "3000000000")
    }

    assert perfiles == {NivelRiesgo.A, NivelRiesgo.B, NivelRiesgo.C}
    assert {
        adaptador.consultar("cc", identificacion).factor_prima
        for identificacion in ("1000000000", "2000000000", "3000000000")
    } == {Decimal("1.00"), Decimal("1.10"), Decimal("1.25")}
