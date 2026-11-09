from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from cotizacion.infrastructure.persistence.database import crear_sesiones
from cotizacion.infrastructure.persistence.models import (
    OutboxEventoModel,
    SuscripcionModel,
)
from cotizacion.infrastructure.publicador_notificaciones import (
    PaymentNotification,
    PaymentNotificationPublisher,
)
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker


class FakeSender:
    def __init__(self, result: bool) -> None:
        self.result = result
        self.sent: list[PaymentNotification] = []

    def send(self, notification: PaymentNotification) -> bool:
        self.sent.append(notification)
        return self.result


def seed_payment_event(
    database_url: str,
) -> tuple[sessionmaker[Session], UUID, tuple[UUID, UUID, datetime]]:
    sessions = crear_sesiones(database_url)
    now = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
    subscription_id = uuid4()
    client_id = uuid4()
    payment_id = uuid4()
    event_id = uuid4()
    with sessions() as session, session.begin():
        session.add(
            SuscripcionModel(
                suscripcion_id=subscription_id,
                solicitud_id=f"SOL-{uuid4().hex[:12]}",
                cotizacion_id=uuid4(),
                cliente_id=client_id,
                oferta_id=uuid4(),
                oferta_version=1,
                estado="aprobada",
                regla_version="1",
                creada_en=now,
                actualizada_en=now,
                idempotency_key=uuid4(),
                request_fingerprint="a" * 64,
            )
        )
        session.add(
            OutboxEventoModel(
                evento_id=event_id,
                tipo="PagoSimuladoConfirmado",
                agregado_id=subscription_id,
                payload={"pagoId": str(payment_id)},
                creado_en=now,
                publicado_en=None,
                intentos=0,
            )
        )
    return sessions, event_id, (client_id, payment_id, now)


def test_publishes_confirmed_payment_once(database_url: str) -> None:
    sessions, event_id, expected = seed_payment_event(database_url)
    sender = FakeSender(True)
    publisher = PaymentNotificationPublisher(sessions, sender)
    published_at = expected[2] + timedelta(seconds=5)

    assert publisher.publish_one(published_at) is True
    assert publisher.publish_one(published_at) is False

    assert len(sender.sent) == 1
    notification = sender.sent[0]
    assert notification.event_id == event_id
    assert (notification.client_id, notification.payment_id, notification.occurred_at) == expected
    with sessions() as session:
        event = session.get(OutboxEventoModel, event_id)
        assert event is not None
        assert event.publicado_en == published_at.replace(tzinfo=None)
        assert event.intentos == 1


def test_failed_publish_retries_with_same_event_id(database_url: str) -> None:
    sessions, event_id, _ = seed_payment_event(database_url)
    sender = FakeSender(False)
    publisher = PaymentNotificationPublisher(sessions, sender)

    assert publisher.publish_one(datetime.now(UTC)) is False
    sender.result = True
    assert publisher.publish_one(datetime.now(UTC)) is True

    assert [notification.event_id for notification in sender.sent] == [event_id, event_id]
    with sessions() as session:
        event = session.scalar(
            select(OutboxEventoModel).where(OutboxEventoModel.evento_id == event_id)
        )
        assert event is not None
        assert event.intentos == 2
        assert event.publicado_en is not None
