from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class ConsentVersionConflictError(Exception):
    pass


class InvalidConsentError(Exception):
    pass


class ConsentScope(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    finalidad: Literal["cotizacion"]
    alcances: tuple[Literal["open_finance", "open_data"], ...] = Field(min_length=1)
    fuentes: tuple[Literal["open_finance", "open_data"], ...] = Field(min_length=1)
    datos: tuple[Literal["indicador_riesgo"], ...] = Field(min_length=1, max_length=1)
    version_terminos: Literal["1.0"] = Field(alias="versionTerminos")
    vigente_hasta: AwareDatetime = Field(alias="vigenteHasta")

    @model_validator(mode="after")
    def validate_sources(self) -> "ConsentScope":
        if (
            len(set(self.alcances)) != len(self.alcances)
            or len(set(self.fuentes)) != len(self.fuentes)
            or not set(self.fuentes).issubset(self.alcances)
        ):
            raise ValueError("Las fuentes deben ser unicas y estar cubiertas por el alcance.")
        return self

    def validate_expiry(self, now: datetime) -> None:
        if not now < self.vigente_hasta <= now + timedelta(days=30):
            raise InvalidConsentError()


class Consent(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    consentimiento_id: UUID = Field(alias="consentimientoId")
    cliente_id: UUID = Field(alias="clienteId")
    version: int = Field(ge=1)
    estado: Literal["autorizado", "rechazado", "revocado", "vencido"]
    finalidad: Literal["cotizacion"] = "cotizacion"
    alcances: tuple[str, ...] = ("open_finance", "open_data")
    efectiva_en: AwareDatetime = Field(alias="efectivaEn")
    vigente_hasta: AwareDatetime = Field(alias="vigenteHasta")
    # Los registros anteriores se conservan, pero no acreditan una decision completa.
    actor_id: UUID | None = Field(default=None, alias="actorId")
    fuentes: tuple[str, ...] = ()
    datos: tuple[str, ...] = ()
    version_terminos: str | None = Field(default=None, alias="versionTerminos")

    def is_current(self, now: datetime) -> bool:
        return (
            self.estado == "autorizado"
            and self.actor_id == self.cliente_id
            and self.version_terminos == "1.0"
            and bool(self.fuentes)
            and set(self.fuentes).issubset(self.alcances)
            and set(self.fuentes).issubset({"open_finance", "open_data"})
            and self.datos == ("indicador_riesgo",)
            and self.efectiva_en <= now < self.vigente_hasta
        )
