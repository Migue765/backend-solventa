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
    oferta: "PartnerOffer"


class PartnerMoney(BaseModel):
    model_config = ConfigDict(extra="forbid")

    monto: str = Field(pattern=r"^[0-9]+(?:\.[0-9]{1,2})?$")
    moneda: str = Field(pattern=r"^[A-Z]{3}$")


class PartnerOffer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    version: int = Field(ge=1)
    nombre: str = Field(min_length=1)
    descripcion: str = Field(min_length=1)
    prima: PartnerMoney
    periodicidad: Literal["mensual", "por_viaje", "por_vuelo", "por_periodo"]
    primaMensual: PartnerMoney | None
    coberturaPrincipal: str = Field(min_length=1)
    deducible: str = Field(min_length=1)
    vigencia: str = Field(min_length=1)
    responsabilidadCivil: str = Field(min_length=1)
    coberturas: tuple[str, ...]
    condiciones: tuple[str, ...]
    beneficiosAdicionales: tuple[str, ...]
    recomendada: Literal[True]
    estado: Literal["disponible"]
    vigenteHasta: datetime
