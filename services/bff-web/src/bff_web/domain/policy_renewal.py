from datetime import UTC, date
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    model_validator,
)

UtcDatetime = Annotated[AwareDatetime, AfterValidator(lambda value: value.astimezone(UTC))]
Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
NonRenewableReason = Literal[
    "producto_no_renovable", "poliza_programada", "poliza_vencida", "fuera_de_ventana"
]


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")


class Period(_Strict):
    start: date = Field(alias="inicio")
    end: date = Field(alias="fin")

    @model_validator(mode="after")
    def validate_order(self) -> Self:
        if self.end < self.start:
            raise ValueError("La vigencia termina antes de iniciar.")
        return self


class Window(_Strict):
    start: date = Field(alias="desde")
    end: date = Field(alias="hasta")


class Coverage(_Strict):
    name: str = Field(alias="nombre", min_length=1)
    description: str = Field(alias="descripcion", min_length=1)


class Premium(_Strict):
    amount: str = Field(alias="monto", pattern=r"^[1-9][0-9]{0,15}$")
    currency: Literal["COP"] = Field(alias="moneda")
    frequency: Literal["mensual"] = Field(alias="periodicidad")


class RenewalOffer(_Strict):
    policy_id: UUID = Field(alias="polizaId")
    product: str = Field(alias="producto", min_length=1)
    previous_end: date = Field(alias="finAnterior")
    new_period: Period = Field(alias="nuevaVigencia")
    coverages: tuple[Coverage, ...] = Field(alias="coberturas", min_length=1)
    premium: Premium = Field(alias="prima")
    conditions: tuple[str, ...] = Field(alias="condiciones", min_length=1)
    requires_payment: StrictBool = Field(alias="requierePago")
    rule_version: str = Field(alias="reglaVersion", min_length=1)
    conditions_hash: Hash = Field(alias="condicionesHash")


class Renewal(_Strict):
    renewal_id: UUID = Field(alias="id")
    policy_id: UUID = Field(alias="polizaId")
    state: Literal["pendiente", "renovada", "rechazada"] = Field(alias="estado")
    reason: Literal["pago_rechazado"] | None = Field(alias="motivo")
    previous_end: date = Field(alias="finAnterior")
    new_period: Period = Field(alias="nuevaVigencia")
    coverages: tuple[Coverage, ...] = Field(alias="coberturas", min_length=1)
    premium: Premium = Field(alias="prima")
    conditions: tuple[str, ...] = Field(alias="condiciones", min_length=1)
    conditions_hash: Hash = Field(alias="condicionesHash")
    rule_version: str = Field(alias="reglaVersion", min_length=1)
    created_at: UtcDatetime = Field(alias="creadaEn")
    updated_at: UtcDatetime = Field(alias="actualizadaEn")
    simulated: Literal[True] = Field(alias="simulada")

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        if (self.state == "rechazada") != (self.reason is not None):
            raise ValueError("Solo una renovacion rechazada informa motivo.")
        return self


class RenewalEligibility(_Strict):
    policy_id: UUID = Field(alias="polizaId")
    policy_type: str = Field(alias="tipo", min_length=1)
    current_period: Period = Field(alias="vigenciaActual")
    renewable: StrictBool = Field(alias="renovable")
    reason: NonRenewableReason | None = Field(alias="motivo")
    window: Window = Field(alias="ventana")
    offer: RenewalOffer | None = Field(alias="oferta")
    last_attempt: Renewal | None = Field(alias="ultimoIntento")

    @model_validator(mode="after")
    def validate_consistency(self) -> Self:
        if self.renewable != (self.reason is None) or self.renewable != (self.offer is not None):
            raise ValueError("La elegibilidad no es coherente.")
        if self.offer is not None and self.offer.policy_id != self.policy_id:
            raise ValueError("La oferta no corresponde a la poliza.")
        return self


class RenewalRequest(_Strict):
    accepts_conditions: StrictBool = Field(alias="aceptaCondiciones")
    conditions_hash: Hash = Field(alias="condicionesHash")

    @model_validator(mode="after")
    def require_acceptance(self) -> Self:
        if self.accepts_conditions is not True:
            raise ValueError("La renovacion requiere aceptacion explicita.")
        return self


class PolicyNotFoundError(Exception):
    """La poliza no existe o no pertenece al cliente."""


class PolicyNotRenewableError(Exception):
    def __init__(self, reason: NonRenewableReason) -> None:
        super().__init__(reason)
        self.reason = reason


class RenewalConflictError(Exception):
    def __init__(self, code: str, renewal_id: UUID | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.renewal_id = renewal_id


class RenewalServiceUnavailableError(Exception):
    """Polizas no respondio un resultado valido; no se confirma la renovacion."""
