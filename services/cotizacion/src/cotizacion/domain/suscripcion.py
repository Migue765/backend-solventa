from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID


class EstadoSuscripcion(StrEnum):
    APROBADA = "aprobada"
    REVISION = "revision"
    RECHAZADA = "rechazada"


class EstadoSagaEmision(StrEnum):
    PAGO_PENDIENTE = "pago_pendiente"
    PAGO_CONFIRMADO = "pago_confirmado"
    EMITIDA = "emitida"
    FALLIDA = "fallida"


@dataclass(frozen=True)
class DecisionSuscripcion:
    suscripcion_id: UUID
    regla_version: str
    resultado: EstadoSuscripcion
    datos_evaluados: dict[str, Any]
    hash_decision: str
    decidida_en: datetime


@dataclass(frozen=True)
class SagaEmision:
    suscripcion_id: UUID
    estado: EstadoSagaEmision
    version: int
    actualizada_en: datetime
    ultimo_error: str | None = None


@dataclass(frozen=True)
class Suscripcion:
    suscripcion_id: UUID
    solicitud_id: str
    cotizacion_id: UUID
    cliente_id: UUID
    oferta_id: UUID
    oferta_version: int
    estado: EstadoSuscripcion
    regla_version: str
    creada_en: datetime
    actualizada_en: datetime
    idempotency_key: UUID
    request_fingerprint: str
    decision: DecisionSuscripcion
    saga: SagaEmision
