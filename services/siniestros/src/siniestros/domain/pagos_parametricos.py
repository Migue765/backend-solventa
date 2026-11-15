import hashlib
import json
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from siniestros.domain import Timestamp


class EntradaPagoParametrico(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DineroIndemnizacion(EntradaPagoParametrico):
    monto: Decimal = Field(gt=0, allow_inf_nan=False, max_digits=18, decimal_places=2)
    moneda: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]


class CasoPagoParametrico(EntradaPagoParametrico):
    casoId: UUID
    siniestroId: UUID
    clienteId: UUID
    polizaId: UUID
    beneficiarioId: UUID
    indemnizacion: DineroIndemnizacion
    fuente: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    eventoId: UUID
    reglaId: UUID
    reglaVersion: int = Field(ge=1)
    decididoEn: Timestamp


class ResultadoProveedorPago(StrEnum):
    CONFIRMADO = "confirmado"
    RECHAZADO = "rechazado"


class RespuestaPagoAmbigua(Exception):
    def __init__(self, codigo: str = "respuesta_ambigua") -> None:
        self.codigo = codigo
        super().__init__(codigo)


class ProveedorPagoParametrico(Protocol):
    def consultar(self, pago_id: UUID, huella: str) -> ResultadoProveedorPago | None: ...

    def ejecutar(
        self,
        pago_id: UUID,
        caso: CasoPagoParametrico,
        huella: str,
    ) -> ResultadoProveedorPago: ...


def huella_pago_parametrico(caso: CasoPagoParametrico) -> str:
    contenido = caso.model_dump(mode="json")
    return hashlib.sha256(
        json.dumps(contenido, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


EstadoPagoParametrico = Literal["pendiente", "confirmado", "rechazado"]
