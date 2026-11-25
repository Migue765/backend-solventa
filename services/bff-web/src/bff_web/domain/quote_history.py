from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from bff_web.domain.partner_operation import PartnerSelectionState


class QuoteHistoryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operationId: UUID | None
    socioId: str | None
    clienteId: UUID
    solicitudId: str = Field(min_length=1, max_length=32)
    cotizacionId: UUID
    productoId: str
    estado: Literal["pendiente", "completada", "rechazada", "vencida"]
    creadaEn: datetime
    actualizadaEn: datetime
    validaHasta: datetime
    completadaEn: datetime | None
    rechazadaEn: datetime | None
    estadoSeleccion: PartnerSelectionState
    pagoEstado: Literal["pendiente", "rechazado", "confirmado"] | None
    emisionEstado: Literal["pago_pendiente", "pago_confirmado", "emitida", "fallida"] | None
    consentimientoId: UUID | None
    consentimientoVersion: int | None

    @model_validator(mode="after")
    def validate_associations(self) -> "QuoteHistoryEntry":
        if any(
            date.tzinfo is None or date.utcoffset() is None
            for date in (
                self.creadaEn,
                self.actualizadaEn,
                self.validaHasta,
                self.completadaEn,
                self.rechazadaEn,
            )
            if date is not None
        ):
            raise ValueError("Historial con fechas sin zona.")
        if self.estadoSeleccion.solicitudId != self.solicitudId or any(
            item is not None and item.solicitudId != self.solicitudId
            for item in (self.estadoSeleccion.seleccion, self.estadoSeleccion.aceptacion)
        ):
            raise ValueError("Seleccion de otra solicitud.")
        if (self.estado == "completada") != (self.completadaEn is not None) or (
            self.estado == "completada"
            and (
                self.pagoEstado != "confirmado"
                or self.estadoSeleccion.aceptacion is None
                or self.emisionEstado not in {"pago_confirmado", "emitida"}
            )
        ):
            raise ValueError("Completada sin resultado final.")
        if (self.estado == "rechazada") != (self.rechazadaEn is not None):
            raise ValueError("Rechazada sin decision explicita.")
        if self.estado == "rechazada" and (
            self.estadoSeleccion.aceptacion is not None
            or self.pagoEstado is not None
            or self.emisionEstado is not None
        ):
            raise ValueError("Rechazo incompatible con contratacion.")
        return self


class QuoteHistory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    solicitudes: tuple[QuoteHistoryEntry, ...]

    @model_validator(mode="after")
    def validate_unique_requests(self) -> "QuoteHistory":
        if len({row.solicitudId for row in self.solicitudes}) != len(self.solicitudes) or len(
            {row.cotizacionId for row in self.solicitudes}
        ) != len(self.solicitudes):
            raise ValueError("Historial con solicitudes duplicadas.")
        return self


class QuoteRejection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    solicitudId: str = Field(min_length=1, max_length=32)
    cotizacionId: UUID
    estado: Literal["rechazada"]
    rechazadaEn: datetime
