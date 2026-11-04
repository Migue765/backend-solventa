from datetime import UTC
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, model_validator

UtcDatetime = Annotated[AwareDatetime, AfterValidator(lambda value: value.astimezone(UTC))]
ClaimType = Literal["asistido", "parametrico"]
ClaimState = Literal[
    "reportado",
    "en_evaluacion",
    "en_revision",
    "en_inspeccion",
    "aprobado",
    "rechazado",
    "pagado",
    "cerrado",
]


class ClaimHistoryEntry(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    state: ClaimState = Field(alias="estado")
    description: str = Field(alias="descripcion", min_length=1)
    occurred_at: UtcDatetime = Field(alias="ocurridoEn")


class ClaimStatus(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    claim_id: UUID = Field(alias="id")
    policy_id: UUID = Field(alias="polizaId")
    claim_type: ClaimType = Field(alias="tipo")
    state: ClaimState = Field(alias="estado")
    description: str = Field(alias="descripcion", min_length=1)
    occurred_at: UtcDatetime = Field(alias="ocurridoEn")
    reported_at: UtcDatetime = Field(alias="reportadoEn")
    updated_at: UtcDatetime = Field(alias="actualizadoEn")
    history: tuple[ClaimHistoryEntry, ...] = Field(alias="historial", min_length=1)

    @model_validator(mode="after")
    def validate_current_state(self) -> Self:
        if self.occurred_at > self.reported_at:
            raise ValueError("El evento no puede ocurrir despues del reporte.")
        latest = self.history[-1]
        if latest.state != self.state or latest.occurred_at != self.updated_at:
            raise ValueError("El estado vigente no coincide con el historial.")
        return self


class ClaimNotFoundError(Exception):
    """El siniestro no existe o no pertenece al cliente."""


class ClaimServiceUnavailableError(Exception):
    """El servicio de siniestros no respondió una consulta válida."""
