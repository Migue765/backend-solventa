from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class ConsentVersionConflictError(Exception):
    pass


class Consent(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    consentimiento_id: UUID = Field(alias="consentimientoId")
    cliente_id: UUID = Field(alias="clienteId")
    version: int = Field(ge=1)
    estado: Literal["autorizado", "revocado"]
    finalidad: Literal["cotizacion"] = "cotizacion"
    alcances: tuple[str, ...] = ("open_finance", "open_data")
    efectiva_en: AwareDatetime = Field(alias="efectivaEn")
    vigente_hasta: AwareDatetime = Field(alias="vigenteHasta")

    def is_current(self, now: datetime) -> bool:
        return self.estado == "autorizado" and self.efectiva_en <= now < self.vigente_hasta
