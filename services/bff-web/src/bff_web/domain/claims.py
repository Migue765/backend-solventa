from datetime import UTC, date
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

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
    claim_type: ClaimType | None = Field(alias="tipo")
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


class ClaimDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    policy_id: UUID = Field(alias="polizaId")
    occurred_at: UtcDatetime = Field(alias="fechaEvento")
    event_type: str = Field(alias="tipoEvento", min_length=1, max_length=100)
    description: str = Field(alias="descripcion", min_length=10, max_length=5000)
    location: str = Field(alias="lugar", min_length=1, max_length=300)

    @field_validator("occurred_at", mode="before")
    @classmethod
    def validate_date_type(cls, value: object) -> object:
        if not isinstance(value, str) or "T" not in value:
            raise ValueError("Se requiere una fecha ISO con zona.")
        return value


class RegistrationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: UUID = Field(alias="operacionId")
    occurred_at: UtcDatetime = Field(alias="fecha")
    http_status: Literal[201] = Field(alias="httpStatus")
    code: None


class ClaimDetail(ClaimDraft):
    id: UUID
    state: Literal["registrado"] = Field(alias="estado")
    registered_at: UtcDatetime = Field(alias="registradoEn")
    last_result: RegistrationResult | None = Field(default=None, alias="ultimoResultado")


class ClaimList(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claims: list[ClaimDetail] = Field(alias="siniestros")


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
