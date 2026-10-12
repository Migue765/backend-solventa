from decimal import Decimal

from cotizacion.domain.cotizacion import CoberturaCotizada
from cotizacion.domain.dinero import Dinero
from cotizacion.domain.tarifa import PerfilRiesgo, ReglaTarifa


class TarificadorPorRegla:
    """prima = (suma asegurada - deducible) * tasa * (1 + score * factor de perfil)."""

    def calcular_prima(
        self, regla: ReglaTarifa, perfil: PerfilRiesgo, cobertura: CoberturaCotizada
    ) -> Dinero:
        base = cobertura.suma_asegurada.monto - cobertura.deducible.monto
        recargo = Decimal(1) + perfil.score * regla.factor_perfil
        return Dinero(base * regla.tasa * recargo, cobertura.suma_asegurada.moneda)
