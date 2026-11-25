from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from cotizacion.domain.ofertas import EstadoSeleccionProvisional


@dataclass(frozen=True)
class ResumenSolicitud:
    operation_id: UUID | None
    socio_id: str | None
    cliente_id: UUID
    solicitud_id: str
    cotizacion_id: UUID
    producto_id: str
    estado: Literal["pendiente", "completada", "rechazada", "vencida"]
    creada_en: datetime
    actualizada_en: datetime
    valida_hasta: datetime
    completada_en: datetime | None
    rechazada_en: datetime | None
    estado_seleccion: EstadoSeleccionProvisional
    pago_estado: Literal["pendiente", "rechazado", "confirmado"] | None
    emision_estado: str | None
    consentimiento_id: UUID | None
    consentimiento_version: int | None


@dataclass(frozen=True)
class RechazoSolicitud:
    solicitud_id: str
    cotizacion_id: UUID
    cliente_id: UUID
    rechazada_en: datetime
    idempotency_key: UUID
