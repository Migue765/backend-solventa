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
        return f"Riesgo {self.nivel.value}"


def perfil_desde_score(score: Decimal) -> PerfilRiesgo:
    nivel = (
        NivelRiesgo.A
        if score <= Decimal("0.5")
        else (NivelRiesgo.B if score <= Decimal("0.75") else NivelRiesgo.C)
    )
    factor = {
        NivelRiesgo.A: Decimal("1.00"),
        NivelRiesgo.B: Decimal("1.10"),
        NivelRiesgo.C: Decimal("1.25"),
    }[nivel]
    return PerfilRiesgo(nivel, factor)
