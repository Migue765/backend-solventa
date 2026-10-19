import calendar
from datetime import UTC, date, datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

TipoPoliza = Literal[
    "automotive",
    "travel",
    "life",
    "device-protection",
    "parametric",
    "parametric-weather",
    "payment-protection",
    "mortgage-life",
]


class FechasContratadas(BaseModel):
    model_config = ConfigDict(extra="forbid")
    inicio: date
    fin: date

    @field_validator("inicio", "fin", mode="before")
    @classmethod
    def validar_formato(cls, valor: object) -> object:
        if not isinstance(valor, str) or len(valor) != 10:
            raise ValueError("Se requiere fecha YYYY-MM-DD.")
        return valor

    @model_validator(mode="after")
    def validar_orden(self) -> Self:
        if self.fin < self.inicio:
            raise ValueError("La fecha fin no puede preceder al inicio.")
        return self


class EvidenciaEmision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cliente_id: UUID = Field(alias="clienteId")
    suscripcion_id: UUID = Field(alias="suscripcionId")
    pago_id: UUID = Field(alias="pagoId")
    tipo: TipoPoliza
    confirmada_en: AwareDatetime = Field(alias="confirmadaEn")
    simulada: Literal[True]
    fechas_contratadas: FechasContratadas | None = Field(alias="fechasContratadas")

    @field_validator("simulada", mode="before")
    @classmethod
    def validar_simulada(cls, valor: object) -> object:
        if valor is not True:
            raise ValueError("Solo se permite emision academica simulada.")
        return valor

    @model_validator(mode="after")
    def validar_fechas(self) -> Self:
        requiere_fechas = self.tipo in {"travel", "parametric", "parametric-weather"}
        if requiere_fechas != (self.fechas_contratadas is not None):
            raise ValueError("Las fechas contratadas no corresponden al producto.")
        if (
            self.tipo == "parametric"
            and self.fechas_contratadas is not None
            and self.fechas_contratadas.inicio != self.fechas_contratadas.fin
        ):
            raise ValueError("La vigencia por vuelo debe coincidir con su fecha.")
        return self

    def vigencia(self) -> tuple[date, date]:
        if self.fechas_contratadas is not None:
            return self.fechas_contratadas.inicio, self.fechas_contratadas.fin
        inicio = self.confirmada_en.astimezone(UTC).date()
        anio = inicio.year + (inicio.month == 12)
        mes = inicio.month % 12 + 1
        fin = date(anio, mes, min(inicio.day, calendar.monthrange(anio, mes)[1]))
        return inicio, fin


def estado_vigencia(inicio: date, fin: date, ahora: datetime) -> str:
    hoy = ahora.astimezone(UTC).date()
    if hoy < inicio:
        return "programada"
    return "vencida" if hoy > fin else "vigente"
