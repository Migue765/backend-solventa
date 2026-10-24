from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PartnerOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operationId: UUID
    socioId: str = Field(min_length=1, max_length=50)
    clienteId: UUID
    productoId: str = Field(min_length=1, max_length=64)
    estado: Literal["completada"]
    perfilamientoReferencia: str = Field(min_length=1, max_length=64)
    solicitudId: str = Field(min_length=1, max_length=32)
    cotizacionId: UUID
    recibidaEn: datetime
