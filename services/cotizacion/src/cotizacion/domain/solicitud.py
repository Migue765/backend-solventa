from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from cotizacion.domain.cotizacion import Cotizacion


@dataclass(frozen=True)
class SolicitudCotizacion:
    solicitud_id: str
    cotizacion: Cotizacion
    tipo_cliente: str
    identificacion: str
    patente_vehiculo: str
    correo: str
    telefono: str
    idempotency_key: UUID
    request_fingerprint: str
    creada_en: datetime
