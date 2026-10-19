from datetime import date
from decimal import Decimal
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

BillingPeriod = Literal["mensual", "por_viaje", "por_vuelo", "por_periodo"]


class DashboardModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DashboardMoney(DashboardModel):
    amount: str = Field(alias="monto", pattern=r"^\d+\.\d{2}$")
    currency: str = Field(alias="moneda", pattern=r"^[A-Z]{3}$")


class DashboardSelection(DashboardModel):
    offer_id: UUID = Field(alias="ofertaId")
    offer_name: str = Field(alias="nombreOferta", min_length=1)
    premium: DashboardMoney = Field(alias="prima")
    billing_period: BillingPeriod = Field(alias="periodicidad")
    selected_at: AwareDatetime = Field(alias="seleccionadaEn")


class DashboardQuote(DashboardModel):
    id: UUID
    request_id: str | None = Field(alias="solicitudId")
    product_id: str = Field(alias="productoId", min_length=1)
    status: str = Field(alias="estado", min_length=1)
    created_at: AwareDatetime = Field(alias="creadaEn")
    valid_until: AwareDatetime = Field(alias="validaHasta")
    estimated_premium: DashboardMoney = Field(alias="primaEstimada")
    selection: DashboardSelection | None = Field(alias="seleccion")


class DashboardSaga(DashboardModel):
    status: str = Field(alias="estado", min_length=1)
    version: Annotated[int, Field(strict=True, ge=1)]
    updated_at: AwareDatetime = Field(alias="actualizadaEn")


class DashboardSubscription(DashboardModel):
    id: UUID
    request_id: str = Field(alias="solicitudId", min_length=1)
    quote_id: UUID = Field(alias="cotizacionId")
    offer_id: UUID = Field(alias="ofertaId")
    status: str = Field(alias="estado", min_length=1)
    created_at: AwareDatetime = Field(alias="creadaEn")
    updated_at: AwareDatetime = Field(alias="actualizadaEn")
    saga: DashboardSaga


class DashboardPayment(DashboardModel):
    id: UUID
    request_id: str = Field(alias="solicitudId", min_length=1)
    subscription_id: UUID = Field(alias="suscripcionId")
    status: Literal["confirmado"] = Field(alias="estado")
    simulated: Literal[True] = Field(alias="simulada")
    amount: DashboardMoney = Field(alias="importe")
    billing_period: BillingPeriod = Field(alias="periodicidad")
    confirmed_at: AwareDatetime = Field(alias="confirmadaEn")

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, value: DashboardMoney) -> DashboardMoney:
        if Decimal(value.amount) <= 0:
            raise ValueError("El importe del pago debe ser positivo")
        return value

    @field_validator("simulated", mode="before")
    @classmethod
    def validate_simulated(cls, value: object) -> object:
        if value is not True:
            raise ValueError("El tablero solo admite pagos simulados")
        return value


class UnimplementedDomain(DashboardModel):
    availability: Literal["no_implementado"] = Field(alias="disponibilidad")


class DashboardPolicy(DashboardModel):
    id: UUID
    subscription_id: UUID = Field(alias="suscripcionId")
    type: Literal[
        "automotive",
        "travel",
        "life",
        "device-protection",
        "parametric",
        "parametric-weather",
        "payment-protection",
        "mortgage-life",
    ] = Field(alias="tipo")
    start_date: date = Field(alias="fechaInicio")
    end_date: date = Field(alias="fechaFin")
    status: Literal["programada", "vigente", "vencida"] = Field(alias="estado")
    simulated: Literal[True] = Field(alias="simulada")

    @field_validator("simulated", mode="before")
    @classmethod
    def validate_simulated(cls, value: object) -> object:
        if value is not True:
            raise ValueError("La poliza debe identificarse como simulada")
        return value

    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def validate_date(cls, value: object) -> object:
        if not isinstance(value, str) or len(value) != 10:
            raise ValueError("Se requiere fecha YYYY-MM-DD")
        return value

    @model_validator(mode="after")
    def validate_dates(self) -> Self:
        if self.end_date < self.start_date:
            raise ValueError("Vigencia de poliza invalida")
        return self


class Dashboard(DashboardModel):
    quotes: list[DashboardQuote] = Field(alias="cotizaciones")
    subscriptions: list[DashboardSubscription] = Field(alias="suscripciones")
    payments: list[DashboardPayment] = Field(alias="pagos")
    policies: list[DashboardPolicy] | UnimplementedDomain = Field(alias="polizas")
    notifications: UnimplementedDomain = Field(alias="notificaciones")
