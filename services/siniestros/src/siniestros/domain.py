from datetime import UTC, date, datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


class ErrorSiniestro(Exception):
    def __init__(self, codigo: str, estado: int) -> None:
        super().__init__(codigo)
        self.codigo = codigo
        self.estado = estado


class AvisoSiniestro(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    poliza_id: UUID = Field(alias="polizaId")
    fecha_ocurrencia: AwareDatetime = Field(alias="fechaEvento")
    tipo_evento: str = Field(alias="tipoEvento", min_length=1, max_length=100)
    descripcion: str = Field(min_length=10, max_length=5000)
    lugar: str = Field(min_length=1, max_length=300)

    @field_validator("fecha_ocurrencia", mode="before")
    @classmethod
    def validar_tipo_fecha(cls, fecha: object) -> object:
        if not isinstance(fecha, (str, datetime)):
            raise ValueError("Se requiere una fecha ISO con zona.")
        return fecha

    @field_validator("fecha_ocurrencia")
    @classmethod
    def normalizar_fecha(cls, fecha: datetime) -> datetime:
        return fecha.astimezone(UTC)


class PolizaAutorizada(BaseModel):
    id: UUID
    tipo: str = Field(min_length=1)
    fecha_inicio: date = Field(alias="fechaInicio")
    fecha_fin: date = Field(alias="fechaFin")
    estado: Literal["programada", "vigente", "vencida"]


class PolizasAutorizadas(BaseModel):
    polizas: list[PolizaAutorizada]


def validar_evento(aviso: AvisoSiniestro, poliza: PolizaAutorizada, ahora: datetime) -> None:
    if poliza.estado != "vigente":
        raise ErrorSiniestro("poliza_no_vigente", 409)
    if (
        aviso.fecha_ocurrencia > ahora
        or not poliza.fecha_inicio <= aviso.fecha_ocurrencia.date() <= poliza.fecha_fin
    ):
        raise ErrorSiniestro("solicitud_invalida", 400)
