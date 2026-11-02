import logging
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from cotizacion.application.ports import (
    PagoSimuladoRepository,
    ProveedorPagoSimulado,
    ReservaPagoSimulado,
)
from cotizacion.domain.errors import ResultadoPagoAmbiguoError
from cotizacion.domain.pago import (
    PagoSimuladoPendiente,
    ResultadoPagoSimulado,
    ResultadoSimuladorPago,
)

logger = logging.getLogger(__name__)


class ProcesarPagoSimulado:
    def __init__(
        self,
        repositorio: PagoSimuladoRepository,
        proveedor: ProveedorPagoSimulado,
        reloj: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repositorio = repositorio
        self._proveedor = proveedor
        self._reloj = reloj

    def confirmar(
        self, solicitud_id: str, cliente_id: UUID, idempotency_key: UUID
    ) -> ResultadoPagoSimulado:
        resultado, nuevo = self._repositorio.reservar_pago_simulado(
            solicitud_id, cliente_id, idempotency_key, self._reloj()
        )
        if not nuevo or not isinstance(resultado, PagoSimuladoPendiente):
            return resultado
        reserva = self._repositorio.reclamar_pago_simulado(self._reloj(), resultado.pago_id)
        if reserva is None:
            return self._repositorio.obtener_intento_pago_simulado(resultado.pago_id)
        return self._resolver(reserva)

    def consultar(
        self, solicitud_id: str, cliente_id: UUID, idempotency_key: UUID | None = None
    ) -> ResultadoPagoSimulado:
        return self._repositorio.obtener_pago_simulado(solicitud_id, cliente_id, idempotency_key)

    def conciliar_uno(self) -> ResultadoPagoSimulado | None:
        reserva = self._repositorio.reclamar_pago_simulado(self._reloj())
        return self._resolver(reserva) if reserva is not None else None

    def _resolver(self, reserva: ReservaPagoSimulado) -> ResultadoPagoSimulado:
        try:
            resultado = self._proveedor.consultar(reserva.pago.pago_id, reserva.huella)
            if resultado is None:
                resultado = self._proveedor.ejecutar(
                    reserva.pago.pago_id, reserva.pago, reserva.huella
                )
            if not isinstance(resultado, ResultadoSimuladorPago):
                raise ResultadoPagoAmbiguoError("respuesta_invalida")
        except ResultadoPagoAmbiguoError as error:
            vigente = self._repositorio.aplazar_pago_simulado(reserva, self._reloj(), error.codigo)
            logger.warning(
                "pago_simulado.pendiente intento_id=%s intento=%s codigo=%s lease_vigente=%s",
                reserva.pago.pago_id,
                reserva.intentos,
                error.codigo,
                vigente,
            )
            return self._repositorio.obtener_intento_pago_simulado(reserva.pago.pago_id)
        return self._repositorio.finalizar_pago_simulado(reserva, resultado, self._reloj())
