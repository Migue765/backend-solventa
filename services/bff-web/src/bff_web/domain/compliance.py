from collections.abc import Sequence
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from bff_web.domain.operational_reports import (
    ReportFilters,
    ReportSnapshot,
    build_report,
    fingerprint,
)

FORMAT = "solventa-regulatorio-academico-v1"
NOTICE = "Simulacion academica; sin validez regulatoria ni conexion con reaseguradores."


class ComplianceError(Exception):
    def __init__(
        self, code: str, status: int, inconsistencies: dict[str, int] | None = None
    ) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.inconsistencies = inconsistencies


class AcademicConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    aceptaSimulacion: Literal[True]

    @field_validator("aceptaSimulacion", mode="before")
    @classmethod
    def confirm(cls, value: object) -> object:
        if value is not True:
            raise ValueError("Se requiere aceptar explicitamente la simulacion.")
        return value


class ExchangeRequest(AcademicConfirmation):
    reporteId: UUID
    escenario: Literal["aceptado", "diferencias"] = "aceptado"


class StoredReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reporteId: UUID
    contenidoHash: str = Field(pattern=r"^[a-f0-9]{64}$")
    generadoEn: AwareDatetime
    contenido: dict[str, Any]

    @model_validator(mode="after")
    def verify(self) -> "StoredReport":
        if (
            fingerprint(self.contenido) != self.contenidoHash
            or self.contenido.get("formatoVersion") != FORMAT
            or self.contenido.get("simulado") is not True
            or self.contenido.get("sinValidezRegulatoria") is not True
        ):
            raise ValueError("La fuente devolvio un reporte no verificable.")
        return self


def academic_report(
    filters: ReportFilters, snapshot: ReportSnapshot, now: datetime
) -> dict[str, Any]:
    report = build_report(filters, snapshot, now)
    known_payments = {policy.pagoId for policy in snapshot.policies}
    report["inconsistencias"]["primasSinPoliza"] = sum(
        premium.pagoId not in known_payments for premium in snapshot.premiums
    )
    if any(report["inconsistencias"].values()):
        raise ComplianceError("datos_inconsistentes", 422, report["inconsistencias"])
    return {
        "formatoVersion": FORMAT,
        "simulado": True,
        "sinValidezRegulatoria": True,
        "aviso": NOTICE,
        "corte": report["corte"],
        "filtros": report["filtros"],
        "indicadores": report["indicadores"],
        "fuentes": report["fuentes"],
        "trazabilidad": {
            "polizas": _trace(snapshot.policies),
            "primas": _trace(snapshot.premiums),
            "siniestros": _trace(snapshot.claims),
            "pagos": _trace(snapshot.payments),
        },
    }


def _trace(records: Sequence[BaseModel]) -> list[dict[str, Any]]:
    content = [record.model_dump(mode="json") for record in records]
    return [
        {"versionHash": fingerprint(item), "datos": item}
        for item in sorted(content, key=fingerprint)
    ]
