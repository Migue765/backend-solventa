from datetime import datetime
from typing import Protocol
from uuid import UUID

from cotizacion.domain.cotizacion import CoberturaCotizada, Cotizacion
from cotizacion.domain.dinero import Dinero
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


class RepositorioReglas(Protocol):
    def obtener_vigente(self, producto_id: UUID, ahora: datetime) -> ReglaTarifa | None: ...


class ProveedorPerfil(Protocol):
    def obtener(self, perfil_id: str, cliente_id: UUID) -> PerfilRiesgo | None: ...


class Tarificador(Protocol):
    def calcular_prima(
        self, regla: ReglaTarifa, perfil: PerfilRiesgo, cobertura: CoberturaCotizada
    ) -> Dinero: ...
