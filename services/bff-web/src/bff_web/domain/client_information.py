from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class ClientInformationEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    source: Literal["open_finance", "open_data"] = Field(alias="fuente")
    version: str
    consulted_at: AwareDatetime = Field(alias="consultadaEn")
    simulated: bool = Field(alias="simulada")


class ClientInformationResult(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    operation_id: UUID = Field(alias="operationId")
    partner_id: str = Field(alias="socioId")
    actor_id: UUID = Field(alias="actorId")
    client_id: UUID = Field(alias="clienteId")
    product_id: str = Field(alias="productoId")
    purpose: Literal["cotizacion"] = Field(alias="finalidad")
    status: Literal["completada"] = Field(alias="estado")
    consent_id: UUID = Field(alias="consentimientoId")
    consent_version: int = Field(alias="consentimientoVersion")
    data: dict[str, str] = Field(alias="datos")
    evidence: tuple[ClientInformationEvidence, ...] = Field(alias="evidenciaFuentes")
    consulted_at: datetime = Field(alias="consultadaEn")


class ClientInformationError(Exception):
    def __init__(self, code: str, status: int) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
