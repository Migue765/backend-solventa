from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID


@dataclass(frozen=True)
class ReglaTarifa:
    """Regla de tarificación versionada: prima = base * tasa * (1 + score * factor_perfil)."""

    regla_id: UUID
    version: str
    producto_id: UUID
    tasa: Decimal
    factor_perfil: Decimal
    vigente_desde: datetime
    vigente_hasta: datetime | None = None

    def vigente_en(self, instante: datetime) -> bool:
        if instante < self.vigente_desde:
            return False
        return self.vigente_hasta is None or instante < self.vigente_hasta


@dataclass(frozen=True)
class PerfilRiesgo:
    perfil_id: str
    score: Decimal
    modelo_version: str
