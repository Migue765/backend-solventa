from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from cotizacion.application.ports import PagoSimuladoRepository
from cotizacion.domain.pago import PagoSimulado


class ProcesarPagoSimulado:
    def __init__(
        self,
        repositorio: PagoSimuladoRepository,
        reloj: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repositorio = repositorio
        self._reloj = reloj

    def confirmar(self, solicitud_id: str, cliente_id: UUID, idempotency_key: UUID) -> PagoSimulado:
        return self._repositorio.confirmar_pago_simulado(
            solicitud_id, cliente_id, idempotency_key, self._reloj()
        )

    def consultar(self, solicitud_id: str, cliente_id: UUID) -> PagoSimulado:
        return self._repositorio.obtener_pago_simulado(solicitud_id, cliente_id)
