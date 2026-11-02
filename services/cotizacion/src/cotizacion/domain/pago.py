import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
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


class ResultadoSimuladorPago(StrEnum):
    CONFIRMADO = "confirmado"
    RECHAZADO = "rechazado"


@dataclass(frozen=True)
class PagoSimuladoRechazado:
    pago_id: UUID
    solicitud_id: str
    suscripcion_id: UUID
    oferta_id: UUID
    oferta_version: int
    importe: Dinero
    periodicidad: str
    rechazada_en: datetime
    saga_version: int
    saga_actualizada_en: datetime


@dataclass(frozen=True)
class PagoSimuladoPendiente:
    pago_id: UUID
    solicitud_id: str
    suscripcion_id: UUID
    cliente_id: UUID
    oferta_id: UUID
    oferta_version: int
    importe: Dinero
    periodicidad: str
    creada_en: datetime
    saga_version: int
    saga_actualizada_en: datetime
    emision_solicitada: bool
    conciliacion_emision_solicitada: bool


ResultadoPagoSimulado = PagoSimulado | PagoSimuladoRechazado | PagoSimuladoPendiente


def huella_pago_simulado(pago: PagoSimuladoPendiente) -> str:
    contenido = {
        "pagoId": str(pago.pago_id),
        "solicitudId": pago.solicitud_id,
        "suscripcionId": str(pago.suscripcion_id),
        "clienteId": str(pago.cliente_id),
        "ofertaId": str(pago.oferta_id),
        "ofertaVersion": pago.oferta_version,
        "importe": {"monto": str(pago.importe.monto), "moneda": pago.importe.moneda},
        "periodicidad": pago.periodicidad,
        "creadaEn": pago.creada_en.astimezone(UTC).isoformat(),
        "sagaVersion": pago.saga_version,
        "sagaActualizadaEn": pago.saga_actualizada_en.astimezone(UTC).isoformat(),
        "emisionSolicitada": pago.emision_solicitada,
        "conciliacionEmisionSolicitada": pago.conciliacion_emision_solicitada,
    }
    return hashlib.sha256(
        json.dumps(contenido, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
