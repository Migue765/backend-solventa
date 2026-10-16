from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class Consentimiento(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    consentimiento_id: UUID = Field(alias="consentimientoId")
    cliente_id: UUID = Field(alias="clienteId")
    version: int = Field(ge=1)
    estado: str
    finalidad: str = "cotizacion"
    alcances: tuple[str, ...] = ("open_finance", "open_data")
    efectiva_en: datetime = Field(alias="efectivaEn")
    vigente_hasta: datetime = Field(alias="vigenteHasta")

    def vigente(self, ahora: datetime) -> bool:
        return (
            self.estado == "autorizado"
            and self.efectiva_en <= ahora < self.vigente_hasta
            and self.finalidad == "cotizacion"
            and set(self.alcances) == {"open_finance", "open_data"}
        )
