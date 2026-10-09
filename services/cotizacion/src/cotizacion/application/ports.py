from typing import Protocol
from uuid import UUID

from cotizacion.domain.cotizacion import CoberturaCotizada, Cotizacion
from cotizacion.domain.dinero import Dinero


class CotizacionRepository(Protocol):
    def guardar(self, cotizacion: Cotizacion) -> None: ...


class Tarificador(Protocol):
    def calcular_prima(self, producto_id: UUID, cobertura: CoberturaCotizada) -> Dinero: ...
