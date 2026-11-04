from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


class ClaimError(Exception):
    def __init__(self, code: str, status: int) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


class ClaimDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    policy_id: UUID = Field(alias="polizaId")
    occurred_at: AwareDatetime = Field(alias="fechaEvento")
    event_type: str = Field(alias="tipoEvento", min_length=1, max_length=100)
    description: str = Field(alias="descripcion", min_length=10, max_length=5000)
    location: str = Field(alias="lugar", min_length=1, max_length=300)

    @field_validator("occurred_at", mode="before")
    @classmethod
    def validate_date_type(cls, value: object) -> object:
        if not isinstance(value, (str, datetime)):
            raise ValueError("Se requiere una fecha ISO con zona.")
        return value


class ClaimSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    policy_id: UUID = Field(alias="polizaId")
    reported_at: AwareDatetime = Field(alias="fechaReporte")
    state: str = Field(alias="estado", min_length=1)


class ClaimHistoryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: str = Field(alias="estado", min_length=1)
    date: AwareDatetime = Field(alias="fecha")
    actor_id: UUID = Field(alias="actorId")


class ClaimDetail(ClaimDraft):
    id: UUID
    state: str = Field(alias="estado", min_length=1)
    registered_at: AwareDatetime = Field(alias="registradoEn")
    history: list[ClaimHistoryEntry] = Field(alias="historial", min_length=1)


class ClaimList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claims: list[ClaimSummary] = Field(alias="siniestros")


class ClaimPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    type: str = Field(alias="tipo", min_length=1)
    start: date = Field(alias="fechaInicio")
    end: date = Field(alias="fechaFin")
    state: Literal["vigente"] = Field(alias="estado")


class ClaimPolicies(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policies: list[ClaimPolicy] = Field(alias="polizas")
