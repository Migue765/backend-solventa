from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class OfertaHipotecariaPersistida:
    operation_id: UUID
    socio_id: str
    oferta_id: UUID
    version: int
    contenido: dict[str, Any]
    evidencia_decision: dict[str, Any]
    hash_oferta: str
    hash_decision: str
    creada_en: datetime
