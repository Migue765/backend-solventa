from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from cotizacion.domain.cotizacion import CoberturaCotizada, Cotizacion
from cotizacion.domain.dinero import Dinero
from cotizacion.domain.ofertas import SeleccionOferta, SeleccionProvisional
from cotizacion.domain.pago import (
    PagoSimulado,
    PagoSimuladoPendiente,
    ResultadoPagoSimulado,
    ResultadoSimuladorPago,
)
from cotizacion.domain.perfil_riesgo import PerfilRiesgo as PerfilRiesgoOferta
from cotizacion.domain.solicitud import SolicitudCotizacion
from cotizacion.domain.suscripcion import Suscripcion
from cotizacion.domain.tarifa import PerfilRiesgo, ReglaTarifa


class CotizacionRepository(Protocol):
    def guardar(self, cotizacion: Cotizacion) -> None: ...


class SolicitudCotizacionRepository(Protocol):
    def guardar_solicitud(self, solicitud: SolicitudCotizacion) -> None: ...

    def buscar_por_idempotencia(
        self, cliente_id: UUID, idempotency_key: UUID
    ) -> SolicitudCotizacion | None: ...

    def obtener_solicitud(
        self, solicitud_id: str, cliente_id: UUID
    ) -> SolicitudCotizacion | None: ...


class SeleccionOfertaRepository(Protocol):
    def guardar_seleccion(
        self, seleccion: SeleccionOferta, cliente_id: UUID, revision: int | None = None
    ) -> SeleccionOferta: ...

    def obtener_seleccion(self, solicitud_id: str) -> SeleccionOferta | None: ...

    def buscar_seleccion_provisional(
        self, cliente_id: UUID, clave: UUID
    ) -> SeleccionProvisional | None: ...

    def guardar_seleccion_provisional(
        self, seleccion: SeleccionProvisional, cliente_id: UUID, revision_esperada: int
    ) -> SeleccionProvisional: ...

    def consultar_estado_seleccion(
        self, solicitud_id: str, cliente_id: UUID
    ) -> tuple[SeleccionProvisional | None, SeleccionOferta | None]: ...


class SuscripcionRepository(Protocol):
    def guardar_suscripcion(self, suscripcion: Suscripcion) -> None: ...

    def obtener_suscripcion(self, solicitud_id: str) -> Suscripcion | None: ...

    def buscar_suscripcion_por_idempotencia(
        self, cliente_id: UUID, idempotency_key: UUID
    ) -> Suscripcion | None: ...


@dataclass(frozen=True)
class ReservaPagoSimulado:
    pago: PagoSimuladoPendiente
    huella: str
    token: UUID
    intentos: int


class PagoSimuladoRepository(Protocol):
    def reservar_pago_simulado(
        self, solicitud_id: str, cliente_id: UUID, idempotency_key: UUID, ahora: datetime
    ) -> tuple[ResultadoPagoSimulado, bool]: ...

    def reclamar_pago_simulado(
        self, ahora: datetime, intento_id: UUID | None = None, lease_segundos: int = 30
    ) -> ReservaPagoSimulado | None: ...

    def finalizar_pago_simulado(
        self, reserva: ReservaPagoSimulado, resultado: ResultadoSimuladorPago, ahora: datetime
    ) -> ResultadoPagoSimulado: ...

    def aplazar_pago_simulado(
        self, reserva: ReservaPagoSimulado, ahora: datetime, codigo: str
    ) -> bool: ...

    def obtener_pago_simulado(
        self, solicitud_id: str, cliente_id: UUID, idempotency_key: UUID | None = None
    ) -> ResultadoPagoSimulado: ...

    def obtener_intento_pago_simulado(self, intento_id: UUID) -> ResultadoPagoSimulado: ...


class ProveedorPagoSimulado(Protocol):
    def ejecutar(
        self, efecto_id: UUID, instantanea: PagoSimuladoPendiente, huella: str
    ) -> ResultadoSimuladorPago: ...

    def consultar(self, efecto_id: UUID, huella: str) -> ResultadoSimuladorPago | None: ...


@dataclass(frozen=True)
class ReservaEmision:
    pago: PagoSimulado
    token: UUID
    intentos: int


class RepositorioEmision(Protocol):
    def evidencia_emision(self, pago: PagoSimulado) -> dict[str, object] | None: ...

    def marcar_emitida(self, pago: PagoSimulado, poliza_id: UUID) -> None: ...


class EmisorPolizas(Protocol):
    def emitir(self, evidencia: dict[str, object], correlation_id: str) -> UUID: ...


class PerfilRiesgoGateway(Protocol):
    def consultar(self, tipo_documento: str, identificacion: str) -> PerfilRiesgoOferta: ...


class RepositorioReglas(Protocol):
    def obtener_vigente(self, producto_id: UUID, ahora: datetime) -> ReglaTarifa | None: ...


class ProveedorPerfil(Protocol):
    def obtener(self, perfil_id: str, cliente_id: UUID) -> PerfilRiesgo | None: ...


class GeneradorPerfil(Protocol):
    def generar(
        self,
        cliente_id: UUID,
        referencia_id: UUID,
        consentimiento_id: UUID | None = None,
        consentimiento_version: int | None = None,
        operation_id: UUID | None = None,
        credito_id: str | None = None,
    ) -> PerfilRiesgo: ...


class Tarificador(Protocol):
    def calcular_prima(
        self, regla: ReglaTarifa, perfil: PerfilRiesgo, cobertura: CoberturaCotizada
    ) -> Dinero: ...
