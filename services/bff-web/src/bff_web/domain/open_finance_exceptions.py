from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class ExceptionApplication(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    state: Literal["esperando_decision", "pendiente", "completada", "bloqueada", "detenida"] = (
        Field(alias="estado")
    )
    attempts: int = Field(alias="intentos", ge=0, le=1, strict=True)
    code: str | None = Field(alias="codigo")
    updated_at: AwareDatetime = Field(alias="actualizadaEn")
    receipt_id: UUID | None = Field(alias="acuseId")


class ExceptionProcess(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    type: Literal["consulta_informacion_cliente"] = Field(alias="tipo")
    requester_id: UUID = Field(alias="actorSolicitanteId")
    product_id: str = Field(alias="productoId")
    purpose: Literal["cotizacion"] = Field(alias="finalidad")
    requested_data: tuple[str, ...] = Field(alias="datosSolicitados")


class LinkedException(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    exception_id: UUID = Field(alias="excepcionId")
    partner_id: str = Field(alias="socioId")
    state: Literal["pendiente", "aprobada", "rechazada"] = Field(alias="estado")
    process: ExceptionProcess = Field(alias="proceso")
    application: ExceptionApplication = Field(alias="aplicacion")


class OpenFinanceExceptionError(Exception):
    def __init__(self, code: str, status: int) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
