from typing import Protocol
from uuid import UUID

from cotizacion.domain.cotizacion import CoberturaCotizada, Cotizacion
from cotizacion.domain.dinero import Dinero
from cotizacion.domain.solicitud import SolicitudCotizacion


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


class Tarificador(Protocol):
    def calcular_prima(self, producto_id: UUID, cobertura: CoberturaCotizada) -> Dinero: ...
