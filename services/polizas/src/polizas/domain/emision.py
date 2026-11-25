import calendar
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal, Self
from uuid import UUID

from polizas.domain.condiciones import PRODUCTOS
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_validator,
    model_validator,
)

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


class CondicionesEmision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan: Literal["esencial", "integral", "premium"]
    prima: str = Field(pattern=r"^[0-9]{1,16}(?:\.0{1,2})?$")
    coberturas: list[str] = Field(min_length=1, max_length=10)

    @field_validator("prima")
    @classmethod
    def prima_positiva(cls, valor: str) -> str:
        if Decimal(valor) <= 0:
            raise ValueError("La prima de emision debe ser positiva.")
        return valor


class ContratoVuelo(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    airline: str = Field(min_length=1, max_length=80)
    flightNumber: str = Field(pattern=r"^[A-Z0-9]{2,10}$")
    flightDate: date
    coverageType: Literal["delay-cancellation", "missed-connection", "comprehensive"]
    ofertaId: UUID
    montoIndemnizacion: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    moneda: Literal["COP"]
    contratadaEn: AwareDatetime
    cancelacionClima: StrictBool


class EvidenciaEmision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cliente_id: UUID = Field(alias="clienteId")
    suscripcion_id: UUID = Field(alias="suscripcionId")
    pago_id: UUID = Field(alias="pagoId")
    tipo: TipoPoliza
    confirmada_en: AwareDatetime = Field(alias="confirmadaEn")
    simulada: Literal[True]
    fechas_contratadas: FechasContratadas | None = Field(alias="fechasContratadas")
    condiciones_emision: CondicionesEmision | None = Field(default=None, alias="condicionesEmision")
    contrato_vuelo: ContratoVuelo | None = Field(default=None, alias="contratoVuelo")

    @field_validator("simulada", mode="before")
    @classmethod
    def validar_simulada(cls, valor: object) -> object:
        if valor is not True:
            raise ValueError("Solo se permite emision academica simulada.")
        return valor

    @model_validator(mode="after")
    def validar_fechas(self) -> Self:
        if self.contrato_vuelo is not None:
            if (
                self.tipo != "parametric"
                or self.fechas_contratadas is None
                or self.contrato_vuelo.flightDate != self.fechas_contratadas.inicio
                or self.contrato_vuelo.contratadaEn != self.confirmada_en
                or self.confirmada_en.astimezone(UTC).date() >= self.contrato_vuelo.flightDate
            ):
                raise ValueError("El contrato de vuelo no corresponde a la compra.")
        if self.condiciones_emision is not None:
            if self.tipo not in PRODUCTOS:
                raise ValueError("Las condiciones mensuales no corresponden al producto.")
            nombres = self.condiciones_emision.coberturas
            permitidas = {c.nombre for c in PRODUCTOS[self.tipo].coberturas}
            if len(set(nombres)) != len(nombres) or not set(nombres) <= permitidas:
                raise ValueError("Las coberturas de emision no corresponden al producto.")
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
        return inicio, mes_calendario_despues(inicio)


def mes_calendario_despues(inicio: date) -> date:
    anio = inicio.year + (inicio.month == 12)
    mes = inicio.month % 12 + 1
    return date(anio, mes, min(inicio.day, calendar.monthrange(anio, mes)[1]))


def estado_vigencia(inicio: date, fin: date, ahora: datetime) -> str:
    hoy = ahora.astimezone(UTC).date()
    if hoy < inicio:
        return "programada"
    return "vencida" if hoy > fin else "vigente"
