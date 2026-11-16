from datetime import date
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

from bff_web.domain.policy_renewal import Hash, Period, Premium, UtcDatetime

Plan = Literal["esencial", "integral", "premium"]
CoverageCode = Annotated[str, Field(pattern=r"^[a-z_]{1,40}$")]
NotModifiableReason = Literal[
    "producto_no_modificable",
    "poliza_cancelada",
    "poliza_programada",
    "poliza_vencida",
    "fin_de_vigencia",
]


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")


class Money(_Strict):
    amount: str = Field(alias="monto", pattern=r"^-?[0-9]{1,16}$")
    currency: Literal["COP"] = Field(alias="moneda")


class PolicyCoverage(_Strict):
    code: CoverageCode = Field(alias="codigo")
    name: str = Field(alias="nombre", min_length=1)
    description: str = Field(alias="descripcion", min_length=1)
    optional: StrictBool = Field(alias="opcional")
    included: StrictBool = Field(alias="incluida")

    @model_validator(mode="after")
    def mandatory_is_included(self) -> Self:
        if not self.optional and not self.included:
            raise ValueError("Una cobertura obligatoria siempre esta incluida.")
        return self


class PolicyVersion(_Strict):
    version: StrictInt = Field(ge=1)
    plan: Plan
    coverages: tuple[PolicyCoverage, ...] = Field(alias="coberturas", min_length=1)
    premium: Premium = Field(alias="prima")
    effective_from: date = Field(alias="efectivoDesde")
    origin: Literal["emision", "modificacion"] = Field(alias="origen")
    endorsement_id: UUID | None = Field(alias="endosoId")
    created_at: UtcDatetime = Field(alias="creadaEn")


class PlanOption(_Strict):
    code: Plan = Field(alias="codigo")
    name: str = Field(alias="nombre", min_length=1)
    factor: str = Field(pattern=r"^[0-9]+\.[0-9]{2}$")


class OptionalCoverage(_Strict):
    code: CoverageCode = Field(alias="codigo")
    name: str = Field(alias="nombre", min_length=1)
    surcharge: Money = Field(alias="recargo")


class ModificationOptions(_Strict):
    plans: tuple[PlanOption, ...] = Field(alias="planes", min_length=1)
    optional_coverages: tuple[OptionalCoverage, ...] = Field(alias="coberturasOpcionales")


class ModificationState(_Strict):
    policy_id: UUID = Field(alias="polizaId")
    policy_type: str = Field(alias="tipo", min_length=1)
    period: Period = Field(alias="vigencia")
    modifiable: StrictBool = Field(alias="modificable")
    reason: NotModifiableReason | None = Field(alias="motivo")
    current_version: PolicyVersion | None = Field(alias="versionActual")
    options: ModificationOptions | None = Field(alias="opciones")
    history: tuple[PolicyVersion, ...] = Field(alias="historial")

    @model_validator(mode="after")
    def validate_consistency(self) -> Self:
        if self.modifiable != (self.reason is None) or self.modifiable != (
            self.options is not None
        ):
            raise ValueError("La elegibilidad de modificacion no es coherente.")
        if self.current_version is None:
            if self.modifiable or self.history:
                raise ValueError("Sin version vigente no hay historial ni modificacion.")
        elif not self.history or self.history[0] != self.current_version:
            raise ValueError("El historial debe iniciar con la version vigente.")
        versions = [item.version for item in self.history]
        if versions != sorted(versions, reverse=True) or len(set(versions)) != len(versions):
            raise ValueError("El historial debe estar ordenado sin versiones repetidas.")
        return self


class PlanChange(_Strict):
    previous: Plan = Field(alias="anterior")
    new: Plan = Field(alias="nuevo")


class ImpactPeriod(Period):
    changes: Literal[False] = Field(alias="cambia")


class ModificationImpact(_Strict):
    base_version: StrictInt = Field(alias="versionBase", ge=1)
    plan: PlanChange
    coverages: tuple[PolicyCoverage, ...] = Field(alias="coberturas", min_length=1)
    added: tuple[CoverageCode, ...] = Field(alias="coberturasAgregadas")
    removed: tuple[CoverageCode, ...] = Field(alias="coberturasRetiradas")
    previous_premium: Money = Field(alias="primaAnterior")
    new_premium: Money = Field(alias="primaNueva")
    monthly_difference: Money = Field(alias="diferenciaMensual")
    prorated_adjustment: Money = Field(alias="ajusteProrrateado")
    remaining_days: StrictInt = Field(alias="diasRestantes", ge=1)
    effective_from: date = Field(alias="efectivoDesde")
    period: ImpactPeriod = Field(alias="vigencia")
    rule_version: str = Field(alias="reglaVersion", min_length=1)
    impact_hash: Hash = Field(alias="impactoHash")

    @model_validator(mode="after")
    def validate_amounts(self) -> Self:
        difference = int(self.new_premium.amount) - int(self.previous_premium.amount)
        if difference != int(self.monthly_difference.amount):
            raise ValueError("La diferencia mensual no corresponde a las primas.")
        if not self.period.start < self.effective_from <= self.period.end:
            raise ValueError("El cambio debe ser efectivo dentro de la vigencia.")
        return self


class ModificationResult(_Strict):
    endorsement_id: UUID = Field(alias="endosoId")
    policy_id: UUID = Field(alias="polizaId")
    previous_version: StrictInt = Field(alias="versionAnterior", ge=1)
    new_version: StrictInt = Field(alias="versionNueva", ge=2)
    version: PolicyVersion
    impact: ModificationImpact = Field(alias="impacto")
    created_at: UtcDatetime = Field(alias="creadoEn")

    @model_validator(mode="after")
    def validate_versions(self) -> Self:
        if (
            self.new_version != self.previous_version + 1
            or self.version.version != self.new_version
            or self.version.endorsement_id != self.endorsement_id
            or self.impact.base_version != self.previous_version
        ):
            raise ValueError("La version confirmada no corresponde al endoso.")
        return self


class ModificationRequest(_Strict):
    base_version: StrictInt = Field(alias="versionBase", ge=1)
    plan: Plan
    optional_coverages: tuple[CoverageCode, ...] = Field(
        alias="coberturasOpcionales", max_length=10
    )


class ModificationConfirmation(ModificationRequest):
    confirms: StrictBool = Field(alias="confirma")
    impact_hash: Hash = Field(alias="impactoHash")

    @model_validator(mode="after")
    def require_confirmation(self) -> Self:
        if self.confirms is not True:
            raise ValueError("La modificacion requiere confirmacion explicita.")
        return self


class PolicyNotModifiableError(Exception):
    def __init__(self, reason: NotModifiableReason) -> None:
        super().__init__(reason)
        self.reason = reason


class ModificationRejectedError(Exception):
    """Regla de negocio: cambio no permitido o sin cambios."""

    def __init__(self, code: str, field: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.field = field


class ModificationConflictError(Exception):
    def __init__(self, code: str, current_version: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.current_version = current_version
