from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OfferFactor(_Strict):
    codigo: str = Field(min_length=1, max_length=40)
    descripcion: str = Field(min_length=1, max_length=200)
    valor: str = Field(min_length=1, max_length=60)


class OfferAmount(_Strict):
    monto: str = Field(pattern=r"^[0-9]+\.[0-9]{2}$")
    moneda: Literal["COP"]


class PartnerOfferPlan(_Strict):
    ofertaId: UUID
    version: int = Field(ge=1)
    nombre: str
    descripcion: str
    prima: OfferAmount
    periodicidad: Literal["mensual", "por_viaje", "por_vuelo", "por_periodo"]
    recomendada: bool
    coberturaPrincipal: str
    deducible: str
    vigencia: str
    coberturas: list[str]
    condiciones: list[str]
    beneficiosAdicionales: list[str]
    factores: list[OfferFactor] = Field(min_length=1)


class OfferDecision(_Strict):
    hashDecision: str = Field(pattern=r"^[0-9a-f]{64}$")
    modelo: str
    reglaId: UUID
    reglaVersion: str


class OfferRisk(_Strict):
    perfilId: UUID
    version: int = Field(ge=1)
    modeloVersion: str
    score: str
    nivel: Literal["A", "B", "C"]
    consentimientoId: UUID
    consentimientoVersion: int = Field(ge=1)
    calculadoEn: datetime
    vigenteHasta: datetime
    fallbackUsed: bool


class OfferSource(_Strict):
    nombre: Literal["open_finance", "open_data"]
    version: str
    consultadaEn: datetime
    vigenteHasta: datetime
    simulada: bool
    indicadorRiesgo: str


class OfferVariables(_Strict):
    baseAsegurada: str
    duracion: int = Field(ge=1)
    periodicidad: Literal["mensual", "por_viaje", "por_vuelo", "por_periodo"]
    tasa: str
    factorPerfil: str
    score: str
    moneda: Literal["COP"]


class PartnerOffer(_Strict):
    """Oferta persistida por Cotizacion para una operacion del socio (SOL-330)."""

    ofertaId: UUID
    operationId: UUID
    socioId: str = Field(min_length=1, max_length=50)
    clienteId: UUID
    productoId: str = Field(min_length=1, max_length=64)
    solicitudId: str = Field(min_length=1, max_length=32)
    cotizacionId: UUID
    estado: Literal["vigente", "vencida"]
    calculadaEn: datetime
    vigenteHasta: datetime
    decision: OfferDecision
    riesgo: OfferRisk
    fuentes: list[OfferSource] = Field(min_length=1)
    variables: OfferVariables
    ofertas: list[PartnerOfferPlan] = Field(min_length=1)
    hashContenido: str = Field(pattern=r"^[0-9a-f]{64}$")
