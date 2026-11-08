import atexit
import logging
from datetime import UTC, datetime
from threading import Event, Thread

from sqlalchemy import Engine, select
from sqlalchemy.exc import SQLAlchemyError

from cotizacion.application.emision import completar_emision_academica
from cotizacion.application.pagos import ProcesarPagoSimulado
from cotizacion.application.ports import EmisorPolizas
from cotizacion.domain.errors import ServicioPolizasNoDisponibleError
from cotizacion.domain.pago import PagoSimulado
from cotizacion.infrastructure.pago_simulado import SimuladorPago
from cotizacion.infrastructure.persistence.database import crear_sesiones
from cotizacion.infrastructure.persistence.models import (
    ConciliacionEmisionModel,
    EfectoPagoSimuladoModel,
    IntentoPagoSimuladoModel,
)
from cotizacion.infrastructure.persistence.repositorio_pagos import RepositorioPagosSqlAlchemy
from cotizacion.infrastructure.publicador_notificaciones import PaymentNotificationPublisher

logger = logging.getLogger(__name__)


class ConciliadorEmision:
    """Recupera exclusivamente pagos academicos con elegibilidad SQL explicita."""

    def __init__(
        self,
        database_url: str,
        polizas: EmisorPolizas | None,
        resultado_pago: str,
        *,
        conciliar_emision: bool = True,
        publicador_notificaciones: PaymentNotificationPublisher | None = None,
    ) -> None:
        self._sesiones = crear_sesiones(
            database_url, pool_size=1, max_overflow=0, limitar_esperas=True
        )
        with self._sesiones() as sesion:
            engine = sesion.get_bind()
            assert isinstance(engine, Engine)
            self._engine = engine
        self.repositorio = RepositorioPagosSqlAlchemy(
            self._sesiones,
            emitir_polizas=polizas is not None,
            conciliar_emision=conciliar_emision,
        )
        self.proveedor = SimuladorPago(self._sesiones, resultado_pago)
        self.pagos = ProcesarPagoSimulado(self.repositorio, self.proveedor)
        self._polizas = polizas
        self._conciliar_emision = conciliar_emision
        self._publicador_notificaciones = publicador_notificaciones
        self._detener = Event()
        self._hilo = Thread(
            target=self._ejecutar, name="conciliacion-emision-academica", daemon=True
        )

    @property
    def activo(self) -> bool:
        return self._hilo.is_alive()

    def iniciar(self) -> None:
        if self._hilo.ident is not None or self._detener.is_set():
            raise RuntimeError("Cada conciliador tiene un unico ciclo de vida.")
        try:
            with self._sesiones() as sesion:
                sesion.execute(select(IntentoPagoSimuladoModel).limit(0))
                sesion.execute(select(EfectoPagoSimuladoModel).limit(0))
                sesion.execute(select(ConciliacionEmisionModel).limit(0))
            self._hilo.start()
        except (SQLAlchemyError, RuntimeError):
            self._engine.dispose()
            raise
        atexit.register(self.cerrar)

    def cerrar(self) -> None:
        self._detener.set()
        if self._hilo.ident is not None:
            self._hilo.join(timeout=60)
        if self.activo:
            logger.critical("conciliacion_emision.cierre_incompleto")
            raise RuntimeError("El conciliador de emision no termino.")
        self._engine.dispose()
        atexit.unregister(self.cerrar)

    def conciliar_uno(self) -> bool:
        try:
            return self._conciliar_pago()
        finally:
            if self._publicador_notificaciones is not None:
                self._publicador_notificaciones.publish_one(datetime.now(UTC))

    def _conciliar_pago(self) -> bool:
        resultado = self.pagos.conciliar_uno()
        if isinstance(resultado, PagoSimulado) and self._polizas is not None:
            try:
                completar_emision_academica(
                    resultado, self.repositorio, self._polizas, str(resultado.pago_id)
                )
            except ServicioPolizasNoDisponibleError:
                logger.error(
                    "conciliacion_emision.polizas_no_disponible pago_id=%s", resultado.pago_id
                )
        if not self._conciliar_emision or self._polizas is None:
            return resultado is not None
        reserva = self.repositorio.reservar_emision(datetime.now(UTC))
        if reserva is None:
            return resultado is not None
        try:
            if not completar_emision_academica(
                reserva.pago, self.repositorio, self._polizas, str(reserva.pago.pago_id)
            ):
                raise ServicioPolizasNoDisponibleError()
        except (ServicioPolizasNoDisponibleError, SQLAlchemyError) as error:
            codigo = (
                "polizas_no_disponible"
                if isinstance(error, ServicioPolizasNoDisponibleError)
                else "persistencia_no_disponible"
            )
            logger.error(
                "conciliacion_emision.fallo pago_id=%s intento=%s codigo=%s",
                reserva.pago.pago_id,
                reserva.intentos,
                codigo,
            )
            vigente = self.repositorio.aplazar_emision(reserva, datetime.now(UTC), codigo)
            if not vigente:
                logger.info(
                    "conciliacion_emision.reserva_superada pago_id=%s", reserva.pago.pago_id
                )
            return False
        logger.info("conciliacion_emision.completada pago_id=%s", reserva.pago.pago_id)
        return True

    def _ejecutar(self) -> None:
        espera = 1.0
        fallos = 0
        try:
            while not self._detener.wait(espera):
                try:
                    self.conciliar_uno()
                except SQLAlchemyError as error:
                    fallos += 1
                    espera = min(2 ** min(fallos, 5), 30)
                    logger.error(
                        "conciliacion_emision.persistencia tipo=%s reintento_en=%s",
                        type(error).__name__,
                        espera,
                    )
                else:
                    fallos, espera = 0, 1.0
        finally:
            if not self._detener.is_set():
                logger.critical("conciliacion_emision.detenida_inesperadamente")
