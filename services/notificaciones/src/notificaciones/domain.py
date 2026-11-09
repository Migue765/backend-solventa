import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

EventType = Literal[
    "pago_parametrico_ejecutado",
    "pago_confirmado",
    "siniestro_estado_cambiado",
    "poliza_emitida",
]
NotificationType = Literal["pagos", "siniestros", "vencimientos", "renovaciones"]
Channel = Literal["in_app", "email", "push"]
ResourceType = Literal["pago", "siniestro", "poliza"]
CLAIM_STATES = frozenset(
    {"en_evaluacion", "en_revision", "en_inspeccion", "aprobado", "rechazado", "pagado", "cerrado"}
)


class Resource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: ResourceType = Field(alias="tipo")
    id: UUID


class NotificationEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: UUID = Field(alias="eventoId")
    type: EventType = Field(alias="tipo")
    client_id: UUID = Field(alias="clienteId")
    occurred_at: AwareDatetime = Field(alias="ocurridoEn")
    resource: Resource = Field(alias="recurso")
    status: str | None = Field(default=None, alias="estado", max_length=30)
    end_date: date | None = Field(default=None, alias="fechaFin")

    @model_validator(mode="after")
    def validate_context(self) -> Self:
        expected = {
            "pago_parametrico_ejecutado": "pago",
            "pago_confirmado": "pago",
            "siniestro_estado_cambiado": "siniestro",
            "poliza_emitida": "poliza",
        }[self.type]
        if self.resource.type != expected:
            raise ValueError("El recurso no corresponde al evento.")
        if self.type == "siniestro_estado_cambiado":
            if self.status not in CLAIM_STATES or self.end_date is not None:
                raise ValueError("El cambio de siniestro no es notificable.")
        elif self.type == "poliza_emitida":
            if self.end_date is None or self.status is not None:
                raise ValueError("La póliza requiere fecha de fin.")
        elif self.status is not None or self.end_date is not None:
            raise ValueError("El pago no admite datos adicionales.")
        return self

    def fingerprint(self) -> str:
        payload = self.model_dump(mode="json", by_alias=True, exclude_none=True)
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


class Preference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: NotificationType = Field(alias="tipo")
    channel: Channel = Field(alias="canal")
    enabled: bool = Field(alias="habilitada")


class PreferenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preferences: list[Preference] = Field(alias="preferencias", min_length=1, max_length=12)

    @model_validator(mode="after")
    def unique_preferences(self) -> Self:
        keys = {(item.type, item.channel) for item in self.preferences}
        if len(keys) != len(self.preferences):
            raise ValueError("Las preferencias no pueden repetirse.")
        return self


@dataclass(frozen=True)
class NotificationDraft:
    type: NotificationType
    title: str
    content: str
    resource_type: ResourceType
    resource_id: UUID
    scheduled_at: datetime


@dataclass(frozen=True)
class Delivery:
    notification_id: UUID
    reservation_id: UUID
    client_id: UUID
    type: NotificationType
    channel: Channel
    title: str
    content: str
    resource_type: ResourceType
    resource_id: UUID
    attempt: int


def drafts(
    event: NotificationEvent,
    now: datetime,
    expiration_notice_days: int,
    renewal_notice_days: int,
) -> tuple[NotificationDraft, ...]:
    instant = _utc(now)
    if event.type in {"pago_parametrico_ejecutado", "pago_confirmado"}:
        title = (
            "Pago paramétrico procesado"
            if event.type == "pago_parametrico_ejecutado"
            else "Pago confirmado"
        )
        return (
            NotificationDraft(
                "pagos",
                title,
                "Consulta el detalle del pago en Solventa.",
                event.resource.type,
                event.resource.id,
                instant,
            ),
        )
    if event.type == "siniestro_estado_cambiado":
        return (
            NotificationDraft(
                "siniestros",
                "Tu siniestro tiene una actualización",
                "Consulta el estado y el historial de tu siniestro en Solventa.",
                event.resource.type,
                event.resource.id,
                instant,
            ),
        )
    assert event.end_date is not None
    end = datetime.combine(event.end_date, time.min, tzinfo=UTC)
    return (
        NotificationDraft(
            "vencimientos",
            "Tu póliza está próxima a vencer",
            "Consulta la vigencia de tu póliza en Solventa.",
            event.resource.type,
            event.resource.id,
            max(instant, end - timedelta(days=expiration_notice_days)),
        ),
        NotificationDraft(
            "renovaciones",
            "Revisa la renovación de tu póliza",
            "Consulta las opciones de renovación disponibles en Solventa.",
            event.resource.type,
            event.resource.id,
            max(instant, end - timedelta(days=renewal_notice_days)),
        ),
    )


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
