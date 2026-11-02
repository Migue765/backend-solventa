import atexit
import logging
from datetime import UTC, datetime
from threading import Event, Thread

from sqlalchemy import Engine, select
from sqlalchemy.exc import SQLAlchemyError

from cotizacion.application.emision import completar_emision_academica
from cotizacion.application.ports import EmisorPolizas
from cotizacion.domain.errors import ServicioPolizasNoDisponibleError
from cotizacion.infrastructure.persistence.database import crear_sesiones
from cotizacion.infrastructure.persistence.models import ConciliacionEmisionModel
from cotizacion.infrastructure.persistence.repositorio_pagos import RepositorioPagosSqlAlchemy

logger = logging.getLogger(__name__)


class ConciliadorEmision:
    """Recupera exclusivamente pagos academicos con elegibilidad SQL explicita."""

    def __init__(self, database_url: str, polizas: EmisorPolizas) -> None:
        self._sesiones = crear_sesiones(
            database_url, pool_size=1, max_overflow=0, limitar_esperas=True
        )
        with self._sesiones() as sesion:
            engine = sesion.get_bind()
            assert isinstance(engine, Engine)
            self._engine = engine
        self.repositorio = RepositorioPagosSqlAlchemy(
            self._sesiones, emitir_polizas=True, conciliar_emision=True
        )
        self._polizas = polizas
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
        reserva = self.repositorio.reservar_emision(datetime.now(UTC))
        if reserva is None:
            return False
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
