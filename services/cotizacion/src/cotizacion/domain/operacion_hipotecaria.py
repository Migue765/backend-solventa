from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID


@dataclass(frozen=True)
class OperacionHipotecaria:
    operation_id: UUID
    socio_id: str
    asesor_id: UUID
    cliente_id: UUID
    consentimiento_id: UUID
    consentimiento_version: int
    referencia_credito: str
    monto_credito: Decimal
    plazo_meses: int
    solicitud_id: str
    cotizacion_id: UUID
    perfilamiento_referencia: str
    estado: str
    request_fingerprint: str
    creada_en: datetime
