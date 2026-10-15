from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


class NivelRiesgo(str, Enum):
    A = "A"
    B = "B"
    C = "C"


@dataclass(frozen=True)
class PerfilRiesgo:
    nivel: NivelRiesgo
    factor_prima: Decimal

    @property
    def tarifa_aplicada(self) -> str:
        return f"Simulada (Riesgo {self.nivel.value})"
