from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from cotizacion.domain.suscripcion import SagaEmision


class MetodoPago(StrEnum):
    TARJETA = "tarjeta"


class EstadoPago(StrEnum):
    CONFIRMADO = "confirmado"


class EstadoPoliza(StrEnum):
    VIGENTE = "vigente"


@dataclass(frozen=True)
class TransaccionPago:
    transaccion_id: UUID
    orden_pago_id: UUID
    estado: EstadoPago
    referencia: str
    procesada_en: datetime


@dataclass(frozen=True)
class OrdenPago:
    orden_pago_id: UUID
    suscripcion_id: UUID
    solicitud_id: str
    cliente_id: UUID
    monto: Decimal
    moneda: str
    metodo_pago: MetodoPago
    estado: EstadoPago
    idempotency_key: UUID
    request_fingerprint: str
    creada_en: datetime
    confirmada_en: datetime
    transaccion: TransaccionPago


@dataclass(frozen=True)
class Poliza:
    poliza_id: UUID
    suscripcion_id: UUID
    cliente_id: UUID
    producto_id: UUID
    numero: str
    estado: EstadoPoliza
    prima_mensual: Decimal
    moneda: str
    vigente_desde: datetime
    vigente_hasta: datetime
    emitida_en: datetime


@dataclass(frozen=True)
class PagoYEmision:
    pago: OrdenPago
    poliza: Poliza
    saga: SagaEmision
