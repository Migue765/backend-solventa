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
    explicacion: "PartnerOfferExplanation"
    trazabilidad: "PartnerOfferTraceability"


class PartnerOfferFactor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nombre: str = Field(min_length=1)
    valor: str = Field(min_length=1)
    impacto: str = Field(min_length=1)


class PartnerOfferExplanation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resumen: str = Field(min_length=1)
    factoresDeterminantes: tuple[PartnerOfferFactor, ...] = Field(min_length=1)


class PartnerOfferSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nombre: Literal["open_finance", "open_data"]
    version: str = Field(min_length=1)


class PartnerOfferTraceability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    esquemaDecision: int = Field(ge=1)
    hashDecision: str = Field(pattern=r"^[0-9a-f]{64}$")
    hashContenido: str = Field(pattern=r"^[0-9a-f]{64}$")
    perfilId: UUID
    perfilVersion: int = Field(ge=1)
    modeloPerfilVersion: str = Field(min_length=1)
    reglaId: UUID
    reglaVersion: str = Field(min_length=1)
    fuentes: tuple[PartnerOfferSource, ...] = Field(min_length=1)
