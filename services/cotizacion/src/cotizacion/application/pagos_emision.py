import hashlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from cotizacion.application.ports import (
    PagoEmisionRepository,
    SeleccionOfertaRepository,
    SolicitudCotizacionRepository,
    SuscripcionRepository,
)
from cotizacion.domain.errors import (
    ConflictoIdempotenciaError,
    PagoEmisionNoEncontradoError,
    ReglaDeNegocioError,
    SeleccionNoEncontradaError,
    SolicitudNoEncontradaError,
    SuscripcionNoEncontradaError,
)
from cotizacion.domain.pago_emision import (
    EstadoPago,
    EstadoPoliza,
    MetodoPago,
    OrdenPago,
    PagoYEmision,
    Poliza,
    TransaccionPago,
)
from cotizacion.domain.suscripcion import EstadoSagaEmision, EstadoSuscripcion, SagaEmision


class ProcesarPagoYEmision:
    def __init__(
        self,
        solicitudes: SolicitudCotizacionRepository,
        selecciones: SeleccionOfertaRepository,
        suscripciones: SuscripcionRepository,
        pagos_emision: PagoEmisionRepository,
        reloj: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._solicitudes = solicitudes
        self._selecciones = selecciones
        self._suscripciones = suscripciones
        self._pagos_emision = pagos_emision
        self._reloj = reloj

    def procesar(
        self,
        solicitud_id: str,
        cliente_id: UUID,
        metodo_pago: MetodoPago,
        idempotency_key: UUID,
    ) -> PagoYEmision:
        solicitud = self._solicitudes.obtener_solicitud(solicitud_id, cliente_id)
        if solicitud is None:
            raise SolicitudNoEncontradaError()
        suscripcion = self._suscripciones.obtener_suscripcion(solicitud_id)
        if suscripcion is None:
            raise SuscripcionNoEncontradaError()
        if suscripcion.estado is not EstadoSuscripcion.APROBADA:
            raise ReglaDeNegocioError("La suscripción debe estar aprobada antes de pagar.")

        fingerprint = hashlib.sha256(
            f"{solicitud_id}:{suscripcion.suscripcion_id}:{metodo_pago.value}".encode()
        ).hexdigest()
        por_clave = self._pagos_emision.buscar_pago_por_idempotencia(
            cliente_id, idempotency_key
        )
        if por_clave is not None:
            if por_clave.pago.request_fingerprint != fingerprint:
                raise ConflictoIdempotenciaError()
            return por_clave

        existente = self._pagos_emision.obtener_pago_emision(solicitud_id)
        if existente is not None:
            return existente

        seleccion = self._selecciones.obtener_seleccion(solicitud_id)
        if seleccion is None:
            raise SeleccionNoEncontradaError()

        ahora = self._reloj()
        orden_id = uuid4()
        poliza_id = uuid4()
        transaccion = TransaccionPago(
            transaccion_id=uuid4(),
            orden_pago_id=orden_id,
            estado=EstadoPago.CONFIRMADO,
            referencia=f"PAY-{uuid4().hex[:16].upper()}",
            procesada_en=ahora,
        )
        pago = OrdenPago(
            orden_pago_id=orden_id,
            suscripcion_id=suscripcion.suscripcion_id,
            solicitud_id=solicitud_id,
            cliente_id=cliente_id,
            monto=seleccion.prima_mensual.monto,
            moneda=seleccion.prima_mensual.moneda,
            metodo_pago=metodo_pago,
            estado=EstadoPago.CONFIRMADO,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            creada_en=ahora,
            confirmada_en=ahora,
            transaccion=transaccion,
        )
        poliza = Poliza(
            poliza_id=poliza_id,
            suscripcion_id=suscripcion.suscripcion_id,
            cliente_id=cliente_id,
            producto_id=solicitud.cotizacion.producto_id,
            numero=f"SOL-{ahora:%Y%m}-{poliza_id.hex[:10].upper()}",
            estado=EstadoPoliza.VIGENTE,
            prima_mensual=seleccion.prima_mensual.monto,
            moneda=seleccion.prima_mensual.moneda,
            vigente_desde=ahora,
            vigente_hasta=ahora + timedelta(days=365),
            emitida_en=ahora,
        )
        resultado = PagoYEmision(
            pago=pago,
            poliza=poliza,
            saga=SagaEmision(
                suscripcion_id=suscripcion.suscripcion_id,
                estado=EstadoSagaEmision.EMITIDA,
                version=suscripcion.saga.version + 2,
                actualizada_en=ahora,
            ),
        )
        self._pagos_emision.guardar_pago_emision(resultado)
        return resultado

    def consultar(self, solicitud_id: str, cliente_id: UUID) -> PagoYEmision:
        if self._solicitudes.obtener_solicitud(solicitud_id, cliente_id) is None:
            raise SolicitudNoEncontradaError()
        resultado = self._pagos_emision.obtener_pago_emision(solicitud_id)
        if resultado is None:
            raise PagoEmisionNoEncontradoError()
        return resultado
