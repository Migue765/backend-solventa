from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from cotizacion.domain.dinero import Dinero


@dataclass(frozen=True)
class PagoSimulado:
    pago_id: UUID
    solicitud_id: str
    suscripcion_id: UUID
    oferta_id: UUID
    oferta_version: int
    importe: Dinero
    periodicidad: str
    confirmada_en: datetime
    saga_version: int
