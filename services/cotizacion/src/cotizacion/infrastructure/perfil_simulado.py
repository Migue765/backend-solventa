from decimal import Decimal
from uuid import UUID

from cotizacion.domain.tarifa import PerfilRiesgo

MODELO_SIMULADO = "perfil-simulado-1"


class ProveedorPerfilSimulado:
    """Sustituto del contexto de Perfilamiento: entrega un score fijo para cualquier perfil."""

    def __init__(self, score: Decimal) -> None:
        self._score = score

    def obtener(self, perfil_id: str, cliente_id: UUID) -> PerfilRiesgo | None:
        if not perfil_id.strip():
            return None
        return PerfilRiesgo(perfil_id=perfil_id, score=self._score, modelo_version=MODELO_SIMULADO)
