import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)
from sqlalchemy.engine import make_url


class Problem(Exception):
    def __init__(self, status: int, code: str) -> None:
        self.status = status
        self.code = code
        super().__init__(code)


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ConfirmedLocation(Input):
    latitud: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitud: float = Field(ge=-180, le=180, allow_inf_nan=False)
    origen: Literal["permiso", "manual"]
    confirmada: Literal[True]
    confirmadaEn: AwareDatetime

    def validate_time(self, now: datetime) -> None:
        instant = self.confirmadaEn.astimezone(UTC)
        if instant > now + timedelta(minutes=5) or instant < now - timedelta(hours=24):
            raise Problem(422, "ubicacion_no_vigente")


class ProviderQuery(Input):
    ubicacion: ConfirmedLocation
    tipoServicio: Annotated[str, StringConstraints(min_length=1, max_length=80)] | None = None
    radioKm: float = Field(default=50, gt=0, le=100, allow_inf_nan=False)


class AssistanceInput(Input):
    prestadorId: UUID
    tipoAsistencia: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    ubicacion: ConfirmedLocation
    polizaId: UUID | None = None
    siniestroId: UUID | None = None

    @model_validator(mode="after")
    def one_resource(self) -> "AssistanceInput":
        if (self.polizaId is None) == (self.siniestroId is None):
            raise ValueError("Se requiere exactamente una poliza o un siniestro.")
        return self


@dataclass(frozen=True)
class Config:
    database_url: str
    internal_token: str
    policies_url: str
    claims_url: str
    dependency_timeout_seconds: float = 5
    max_provider_distance_km: float = 100

    def __post_init__(self) -> None:
        if not self.internal_token.strip():
            raise ValueError("INTERNAL_API_TOKEN es obligatorio.")
        if make_url(self.database_url).get_backend_name() not in {"sqlite", "postgresql"}:
            raise ValueError("Asistencias solo admite SQLite y PostgreSQL.")
        if not self.policies_url.startswith(("http://", "https://")):
            raise ValueError("POLIZAS_SERVICE_URL debe ser HTTP.")
        if not self.claims_url.startswith(("http://", "https://")):
            raise ValueError("SINIESTROS_SERVICE_URL debe ser HTTP.")
        if self.dependency_timeout_seconds <= 0 or self.max_provider_distance_km <= 0:
            raise ValueError("Los limites de Asistencias deben ser positivos.")

    @classmethod
    def from_environment(cls) -> "Config":
        return cls(
            database_url=os.environ["ASISTENCIAS_DATABASE_URL"],
            internal_token=os.environ["INTERNAL_API_TOKEN"],
            policies_url=os.environ["POLIZAS_SERVICE_URL"].rstrip("/"),
            claims_url=os.environ["SINIESTROS_SERVICE_URL"].rstrip("/"),
            dependency_timeout_seconds=float(os.getenv("DEPENDENCY_TIMEOUT_SECONDS", "5")),
            max_provider_distance_km=float(os.getenv("ASISTENCIAS_DISTANCIA_MAX_KM", "100")),
        )


def fingerprint(payload: dict[str, Any]) -> str:
    import hashlib
    import json

    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
