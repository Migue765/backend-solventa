import json
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from http.client import HTTPException
from typing import Literal, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import UUID

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from cotizacion.infrastructure.persistence.models import (
    OutboxEventoModel,
    SuscripcionModel,
)

logger = logging.getLogger(__name__)


EventType = Literal["pago_confirmado", "poliza_emitida"]
OUTBOX_TYPES: dict[str, tuple[EventType, str, str]] = {
    "PagoSimuladoConfirmado": ("pago_confirmado", "pago", "pagoId"),
    "PolizaAcademicaEmitida": ("poliza_emitida", "poliza", "polizaId"),
}


@dataclass(frozen=True)
class OutboxNotification:
    event_id: UUID
    event_type: EventType
    client_id: UUID
    resource_type: str
    resource_id: UUID
    occurred_at: datetime
    end_date: date | None = None


class NotificationSender(Protocol):
    def send(self, notification: OutboxNotification) -> bool: ...


class NotificationHttpSender:
    def __init__(self, base_url: str, internal_token: str, timeout_seconds: float = 5) -> None:
        self._url = f"{base_url.rstrip('/')}/v1/eventos"
        self._internal_token = internal_token
        self._timeout_seconds = timeout_seconds

    def send(self, notification: OutboxNotification) -> bool:
        event: dict[str, object] = {
            "eventoId": str(notification.event_id),
            "tipo": notification.event_type,
            "clienteId": str(notification.client_id),
            "ocurridoEn": _iso(notification.occurred_at),
            "recurso": {"tipo": notification.resource_type, "id": str(notification.resource_id)},
        }
        if notification.end_date is not None:
            event["fechaFin"] = notification.end_date.isoformat()
        body = json.dumps(event, separators=(",", ":")).encode()
        request = Request(
            self._url,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._internal_token}",
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
            },
            data=body,
        )
        try:
            try:
                response = urlopen(request, timeout=self._timeout_seconds)
            except HTTPError as error:
                response = error
            with response:
                payload = response.read(16 * 1024 + 1)
                if response.status not in {200, 201} or len(payload) > 16 * 1024:
                    return False
                result = json.loads(payload)
                return isinstance(result, dict) and result.get("eventoId") == str(
                    notification.event_id
                )
        except (URLError, TimeoutError, HTTPException, ValueError, TypeError):
            logger.exception(
                "notificaciones.publicacion_fallida evento_id=%s", notification.event_id
            )
            return False


class OutboxNotificationPublisher:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        sender: NotificationSender,
    ) -> None:
        self._sessions = sessions
        self._sender = sender

    def publish_one(self, now: datetime) -> bool:
        with self._sessions() as session, session.begin():
            row = session.execute(
                select(OutboxEventoModel, SuscripcionModel.cliente_id)
                .join(
                    SuscripcionModel,
                    SuscripcionModel.suscripcion_id == OutboxEventoModel.agregado_id,
                )
                .where(
                    or_(
                        OutboxEventoModel.tipo == "PagoSimuladoConfirmado",
                        # Las emisiones previas a la fecha de fin no pueden programar avisos.
                        and_(
                            OutboxEventoModel.tipo == "PolizaAcademicaEmitida",
                            OutboxEventoModel.payload["fechaFin"].as_string().is_not(None),
                        ),
                    ),
                    OutboxEventoModel.publicado_en.is_(None),
                )
                .order_by(OutboxEventoModel.creado_en, OutboxEventoModel.evento_id)
                .limit(1)
            ).one_or_none()
            if row is None:
                return False
            event, client_id = row
            event.intentos += 1
            event_type, resource_type, id_field = OUTBOX_TYPES[event.tipo]
            notification = OutboxNotification(
                event.evento_id,
                event_type,
                client_id,
                resource_type,
                _uuid(event.payload, id_field),
                _utc(event.creado_en),
                _end_date(event.payload) if event_type == "poliza_emitida" else None,
            )

        if not self._sender.send(notification):
            return False

        with self._sessions() as session, session.begin():
            session.execute(
                update(OutboxEventoModel)
                .where(
                    OutboxEventoModel.evento_id == notification.event_id,
                    OutboxEventoModel.publicado_en.is_(None),
                )
                .values(publicado_en=_utc(now))
            )
        return True


def _uuid(payload: dict[str, object], field: str) -> UUID:
    value = payload.get(field)
    if not isinstance(value, str):
        raise ValueError(f"El evento no contiene {field}.")
    return UUID(value)


def _end_date(payload: dict[str, object]) -> date:
    value = payload.get("fechaFin")
    if not isinstance(value, str):
        raise ValueError("El evento de poliza no contiene fechaFin.")
    return date.fromisoformat(value)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")
