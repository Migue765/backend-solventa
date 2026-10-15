from datetime import datetime
from typing import Protocol
from uuid import UUID

from cotizacion.domain.cotizacion import CoberturaCotizada, Cotizacion
from cotizacion.domain.dinero import Dinero
from cotizacion.domain.ofertas import SeleccionOferta
from cotizacion.domain.perfil_riesgo import PerfilRiesgo as PerfilRiesgoOferta
from cotizacion.domain.solicitud import SolicitudCotizacion
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
    def guardar_seleccion(self, seleccion: SeleccionOferta) -> None: ...

    def obtener_seleccion(self, solicitud_id: str) -> SeleccionOferta | None: ...


class PerfilRiesgoGateway(Protocol):
    def consultar(self, tipo_documento: str, identificacion: str) -> PerfilRiesgoOferta: ...


class RepositorioReglas(Protocol):
    def obtener_vigente(self, producto_id: UUID, ahora: datetime) -> ReglaTarifa | None: ...


class ProveedorPerfil(Protocol):
    def obtener(self, perfil_id: str, cliente_id: UUID) -> PerfilRiesgo | None: ...


class Tarificador(Protocol):
    def calcular_prima(
        self, regla: ReglaTarifa, perfil: PerfilRiesgo, cobertura: CoberturaCotizada
    ) -> Dinero: ...
