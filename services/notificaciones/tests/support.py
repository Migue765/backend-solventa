from datetime import UTC, datetime

from notificaciones.domain import Delivery
from notificaciones.service import DeliveryResult


class MutableClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


class FakeDeliveryGateway:
    def __init__(self) -> None:
        self.results: list[DeliveryResult] = []
        self.deliveries: list[Delivery] = []

    def send(self, delivery: Delivery) -> DeliveryResult:
        self.deliveries.append(delivery)
        if self.results:
            return self.results.pop(0)
        return DeliveryResult(True)
