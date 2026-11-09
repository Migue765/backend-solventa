from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from notificaciones.domain import Delivery, NotificationEvent, drafts
from notificaciones.repository import NotificationRepository


@dataclass(frozen=True)
class DeliveryResult:
    delivered: bool
    error: str | None = None


class DeliveryGateway(Protocol):
    def send(self, delivery: Delivery) -> DeliveryResult: ...


class ConfiguredDeliveryGateway:
    def send(self, delivery: Delivery) -> DeliveryResult:
        if delivery.channel == "in_app":
            return DeliveryResult(True)
        return DeliveryResult(False, "proveedor_no_configurado")


class NotificationService:
    def __init__(
        self,
        repository: NotificationRepository,
        delivery_gateway: DeliveryGateway,
        expiration_notice_days: int,
        renewal_notice_days: int,
        max_attempts: int,
    ) -> None:
        self._repository = repository
        self._delivery_gateway = delivery_gateway
        self._expiration_notice_days = expiration_notice_days
        self._renewal_notice_days = renewal_notice_days
        self._max_attempts = max_attempts

    def receive(self, event: NotificationEvent, now: datetime) -> tuple[bool, int, int]:
        generated = drafts(
            event,
            now,
            self._expiration_notice_days,
            self._renewal_notice_days,
        )
        duplicate, count = self._repository.store_event(event, generated, now)
        delivered = self.dispatch(now, limit=20)
        return duplicate, count, delivered

    def dispatch(self, now: datetime, limit: int) -> int:
        delivered = 0
        for _ in range(limit):
            notification = self._repository.reserve_due(now, self._max_attempts)
            if notification is None:
                break
            result = self._delivery_gateway.send(notification)
            self._repository.complete_delivery(
                notification.notification_id,
                notification.reservation_id,
                now,
                result.delivered,
                result.error,
                self._max_attempts,
            )
            delivered += int(result.delivered)
        return delivered
