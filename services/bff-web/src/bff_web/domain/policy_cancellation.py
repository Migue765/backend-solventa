from datetime import date
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

from bff_web.domain.policy_modification import CoverageCode, Money
from bff_web.domain.policy_renewal import Hash, Period, UtcDatetime

NotCancellableReason = Literal[
    "producto_no_cancelable",
    "poliza_cancelada",
    "poliza_programada",
    "poliza_vencida",
    "fin_de_vigencia",
    "renovacion_en_curso",
]
CancellationReason = Literal[
    "ya_no_la_necesito", "costo", "cambio_de_aseguradora", "insatisfaccion", "otro"
]


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")


class EndedCoverage(_Strict):
    code: CoverageCode = Field(alias="codigo")
    name: str = Field(alias="nombre", min_length=1)


class VoidedPeriod(Period):
    premium: Money = Field(alias="prima")


class Settlement(_Strict):
    effective_date: date = Field(alias="fechaEfectiva")
    coverage_end: date = Field(alias="finCobertura")
    ended_coverages: tuple[EndedCoverage, ...] = Field(alias="coberturasFinalizadas", min_length=1)
    current_period: Period = Field(alias="periodoActual")
    voided_periods: tuple[VoidedPeriod, ...] = Field(alias="periodosAnulados")
    monthly_premium: Money = Field(alias="primaMensual")
    period_days: StrictInt = Field(alias="diasPeriodo", ge=1)
    unused_days: StrictInt = Field(alias="diasNoUsados", ge=0)
    prorated_refund: Money = Field(alias="devolucionProporcional")
    voided_refund: Money = Field(alias="devolucionPeriodosAnulados")
    refund: Money = Field(alias="devolucion")
    pending_amount: Money = Field(alias="valorPendiente")
    rule_version: str = Field(alias="reglaVersion", min_length=1)
    settlement_hash: Hash = Field(alias="liquidacionHash")

    @model_validator(mode="after")
    def validate_amounts(self) -> Self:
        amounts = (
            self.prorated_refund,
            self.voided_refund,
            self.refund,
            self.pending_amount,
            *(period.premium for period in self.voided_periods),
        )
        if any(int(item.amount) < 0 for item in amounts):
            raise ValueError("La liquidacion no admite valores negativos.")
        if int(self.refund.amount) != int(self.prorated_refund.amount) + int(
            self.voided_refund.amount
        ):
            raise ValueError("La devolucion no corresponde a sus componentes.")
        if int(self.voided_refund.amount) != sum(
            int(period.premium.amount) for period in self.voided_periods
        ):
            raise ValueError("Los periodos anulados no corresponden a su devolucion.")
        if (
            self.coverage_end != self.effective_date
            or self.unused_days > self.period_days
            or not self.current_period.start <= self.effective_date <= self.current_period.end
        ):
            raise ValueError("La fecha efectiva debe estar dentro del periodo actual.")
        return self


class Cancellation(_Strict):
    cancellation_id: UUID = Field(alias="id")
    policy_id: UUID = Field(alias="polizaId")
    state: Literal["cancelada"] = Field(alias="estado")
    reason: CancellationReason = Field(alias="motivoCancelacion")
    effective_date: date = Field(alias="fechaEfectiva")
    settlement: Settlement = Field(alias="liquidacion")
    confirmed_at: UtcDatetime = Field(alias="confirmadaEn")
    simulated: Literal[True] = Field(alias="simulada")

    @model_validator(mode="after")
    def validate_settlement(self) -> Self:
        if self.settlement.effective_date != self.effective_date:
            raise ValueError("La fecha efectiva no corresponde a la liquidacion.")
        return self


class CancellationState(_Strict):
    policy_id: UUID = Field(alias="polizaId")
    policy_type: str = Field(alias="tipo", min_length=1)
    state: Literal["activa", "cancelada"] = Field(alias="estado")
    period: Period = Field(alias="vigencia")
    cancellable: StrictBool = Field(alias="cancelable")
    reason: NotCancellableReason | None = Field(alias="motivo")
    settlement: Settlement | None = Field(alias="liquidacion")
    cancellation: Cancellation | None = Field(alias="cancelacion")

    @model_validator(mode="after")
    def validate_consistency(self) -> Self:
        if self.cancellable != (self.reason is None) or self.cancellable != (
            self.settlement is not None
        ):
            raise ValueError("La elegibilidad de cancelacion no es coherente.")
        if (self.state == "cancelada") != (self.reason == "poliza_cancelada"):
            raise ValueError("Solo una poliza cancelada informa ese motivo.")
        if self.cancellation is not None and (
            self.state != "cancelada" or self.cancellation.policy_id != self.policy_id
        ):
            raise ValueError("La cancelacion no corresponde a la poliza.")
        return self


class CancellationConfirmation(_Strict):
    confirms: StrictBool = Field(alias="confirma")
    reason: CancellationReason = Field(alias="motivoCancelacion")
    settlement_hash: Hash = Field(alias="liquidacionHash")

    @model_validator(mode="after")
    def require_confirmation(self) -> Self:
        if self.confirms is not True:
            raise ValueError("La cancelacion requiere confirmacion explicita.")
        return self


class PolicyNotCancellableError(Exception):
    def __init__(self, reason: NotCancellableReason) -> None:
        super().__init__(reason)
        self.reason = reason


class CancellationConflictError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code
