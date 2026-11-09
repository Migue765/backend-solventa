import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from http.client import HTTPException
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from cotizacion.infrastructure.persistence.models import (
    OutboxEventoModel,
    SuscripcionModel,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PaymentNotification:
    event_id: UUID
    client_id: UUID
    payment_id: UUID
    occurred_at: datetime


class NotificationSender(Protocol):
    def send(self, notification: PaymentNotification) -> bool: ...


class NotificationHttpSender:
    def __init__(self, base_url: str, internal_token: str, timeout_seconds: float = 5) -> None:
        self._url = f"{base_url.rstrip('/')}/v1/eventos"
        self._internal_token = internal_token
        self._timeout_seconds = timeout_seconds

    def send(self, notification: PaymentNotification) -> bool:
        body = json.dumps(
            {
                "eventoId": str(notification.event_id),
                "tipo": "pago_confirmado",
                "clienteId": str(notification.client_id),
                "ocurridoEn": _iso(notification.occurred_at),
                "recurso": {"tipo": "pago", "id": str(notification.payment_id)},
            },
            separators=(",", ":"),
        ).encode()
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


class PaymentNotificationPublisher:
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
                    OutboxEventoModel.tipo == "PagoSimuladoConfirmado",
                    OutboxEventoModel.publicado_en.is_(None),
                )
                .order_by(OutboxEventoModel.creado_en, OutboxEventoModel.evento_id)
                .limit(1)
            ).one_or_none()
            if row is None:
                return False
            event, client_id = row
            event.intentos += 1
            notification = PaymentNotification(
                event.evento_id,
                client_id,
                _payment_id(event.payload),
                _utc(event.creado_en),
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


def _payment_id(payload: dict[str, object]) -> UUID:
    value = payload.get("pagoId")
    if not isinstance(value, str):
        raise ValueError("El evento de pago no contiene pagoId.")
    return UUID(value)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")
