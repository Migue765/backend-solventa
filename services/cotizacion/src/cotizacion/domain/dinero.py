from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from cotizacion.domain.errors import ReglaDeNegocioError

CENTAVOS = Decimal("0.01")


@dataclass(frozen=True)
class Dinero:
    monto: Decimal
    moneda: str

    def __post_init__(self) -> None:
        if self.monto < 0:
            raise ReglaDeNegocioError("El monto no puede ser negativo.")
        object.__setattr__(self, "monto", self.monto.quantize(CENTAVOS, rounding=ROUND_HALF_UP))

    @classmethod
    def cero(cls, moneda: str) -> Dinero:
        return cls(Decimal("0"), moneda)

    def __add__(self, otro: Dinero) -> Dinero:
        if self.moneda != otro.moneda:
            raise ReglaDeNegocioError("No se pueden sumar montos en monedas distintas.")
        return Dinero(self.monto + otro.monto, self.moneda)
