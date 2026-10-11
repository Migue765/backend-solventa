from decimal import Decimal
from uuid import UUID

from cotizacion.domain.cotizacion import CoberturaCotizada
from cotizacion.domain.dinero import Dinero


class TarificadorTasaFija:
    """Tarifa provisional hasta integrar el Motor de Tarificación con el perfil de riesgo.

    prima = (suma asegurada - deducible) * tasa
    """

    def __init__(self, tasa: Decimal) -> None:
        self._tasa = tasa

    def calcular_prima(self, producto_id: UUID, cobertura: CoberturaCotizada) -> Dinero:
        base = cobertura.suma_asegurada.monto - cobertura.deducible.monto
        return Dinero(base * self._tasa, cobertura.suma_asegurada.moneda)
