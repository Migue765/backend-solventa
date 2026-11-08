from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

from siniestros.domain import Timestamp

TextoCorto = Annotated[str, StringConstraints(min_length=1, max_length=100)]
ValorMetrica = Annotated[
    Decimal,
    Field(allow_inf_nan=False, max_digits=18, decimal_places=6),
]


class EntradaParametrica(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class MetricaEvento(EntradaParametrica):
    nombre: TextoCorto
    valor: ValorMetrica
    unidad: Annotated[str, StringConstraints(min_length=1, max_length=30)]


class EventoParametrico(EntradaParametrica):
    eventoId: UUID
    ocurridoEn: Timestamp
    tipoEvento: TextoCorto
    ubicacionCodigo: TextoCorto
    metrica: MetricaEvento


class LoteEventosParametricos(EntradaParametrica):
    eventos: list[EventoParametrico] = Field(min_length=1, max_length=5_000)


class ReglaParametrica(EntradaParametrica):
    reglaId: UUID
    version: int = Field(ge=1)
    fuente: TextoCorto
    tipoEvento: TextoCorto
    metrica: TextoCorto
    operador: Literal["gt", "gte", "lt", "lte", "eq"]
    umbral: ValorMetrica
    unidad: Annotated[str, StringConstraints(min_length=1, max_length=30)]
    ubicacionCodigo: TextoCorto | None = None
    clienteId: UUID
    polizaId: UUID
    vigenteDesde: Timestamp
    vigenteHasta: Timestamp | None = None


class LimiteProcesamiento(EntradaParametrica):
    limite: int = Field(default=5_000, ge=1, le=5_000)


class ConfirmacionCaso(EntradaParametrica):
    entregadoEn: AwareDatetime


def cumple(operador: str, valor: Decimal, umbral: Decimal) -> bool:
    comparadores = {
        "gt": Decimal.__gt__,
        "gte": Decimal.__ge__,
        "lt": Decimal.__lt__,
        "lte": Decimal.__le__,
        "eq": Decimal.__eq__,
    }
    return bool(comparadores[operador](valor, umbral))
