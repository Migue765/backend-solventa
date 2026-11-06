from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from notificaciones.domain import (
    Channel,
    Delivery,
    NotificationDraft,
    NotificationEvent,
    NotificationType,
    Preference,
    ResourceType,
)
from notificaciones.models import (
    DeliveryAttemptModel,
    NotificationEventModel,
    NotificationModel,
    NotificationPreferenceModel,
)

TYPES: tuple[NotificationType, ...] = ("pagos", "siniestros", "vencimientos", "renovaciones")
CHANNELS: tuple[Channel, ...] = ("in_app", "email", "push")


class EventConflictError(Exception):
    pass


class NotificationRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def store_event(
        self,
        event: NotificationEvent,
        generated: tuple[NotificationDraft, ...],
        now: datetime,
    ) -> tuple[bool, int]:
        instant = _utc(now)
        with Session(self._engine) as session, session.begin():
            stored = session.get(NotificationEventModel, event.event_id)
            if stored is not None:
                if stored.huella != event.fingerprint():
                    raise EventConflictError
                total = session.scalar(
                    select(func.count())
                    .select_from(NotificationModel)
                    .where(NotificationModel.evento_id == event.event_id)
                )
                return True, total or 0

            model = NotificationEventModel(
                evento_id=event.event_id,
                tipo=event.type,
                cliente_id=event.client_id,
                ocurrido_en=_utc(event.occurred_at),
                recibido_en=instant,
                huella=event.fingerprint(),
                resultado="ignorado",
            )
            session.add(model)
            total = 0
            for draft in generated:
                for channel in self._enabled_channels(session, event.client_id, draft.type):
                    session.add(
                        NotificationModel(
                            notificacion_id=uuid4(),
                            evento_id=event.event_id,
                            cliente_id=event.client_id,
                            tipo=draft.type,
                            canal=channel,
                            titulo=draft.title,
                            contenido=draft.content,
                            recurso_tipo=draft.resource_type,
                            recurso_id=draft.resource_id,
                            programada_en=_utc(draft.scheduled_at),
                            estado="pendiente",
                            intentos=0,
                            proximo_intento_en=_utc(draft.scheduled_at),
                            entregada_en=None,
                            ultimo_error=None,
                            reserva_id=None,
                            creada_en=instant,
                        )
                    )
                    total += 1
            model.resultado = "generado" if total else "ignorado"
            return False, total

    def reserve_due(self, now: datetime, max_attempts: int) -> Delivery | None:
        instant = _utc(now)
        with Session(self._engine) as session, session.begin():
            stale_reservations = session.scalars(
                select(NotificationModel).where(
                    NotificationModel.estado == "procesando",
                    NotificationModel.proximo_intento_en <= instant,
                )
            ).all()
            for stale in stale_reservations:
                stale.estado = "fallida" if stale.intentos >= max_attempts else "pendiente"
                stale.reserva_id = None
                stale.ultimo_error = "reserva_expirada"
                session.add(
                    DeliveryAttemptModel(
                        intento_id=uuid4(),
                        notificacion_id=stale.notificacion_id,
                        intentado_en=instant,
                        resultado=stale.estado,
                        error=stale.ultimo_error,
                    )
                )
            statement = (
                select(NotificationModel)
                .where(
                    NotificationModel.estado == "pendiente",
                    NotificationModel.proximo_intento_en <= instant,
                    NotificationModel.intentos < max_attempts,
                )
                .order_by(NotificationModel.proximo_intento_en, NotificationModel.creada_en)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            model = session.scalar(statement)
            if model is None:
                return None
            reservation_id = uuid4()
            model.estado = "procesando"
            model.intentos += 1
            model.reserva_id = reservation_id
            model.proximo_intento_en = instant + timedelta(minutes=5)
            return Delivery(
                model.notificacion_id,
                reservation_id,
                model.cliente_id,
                _notification_type(model.tipo),
                _channel(model.canal),
                model.titulo,
                model.contenido,
                _resource_type(model.recurso_tipo),
                model.recurso_id,
                model.intentos,
            )

    def complete_delivery(
        self,
        notification_id: UUID,
        reservation_id: UUID,
        now: datetime,
        delivered: bool,
        error: str | None,
        max_attempts: int,
    ) -> None:
        instant = _utc(now)
        with Session(self._engine) as session, session.begin():
            model = session.get(NotificationModel, notification_id)
            if model is None or model.estado != "procesando" or model.reserva_id != reservation_id:
                raise RuntimeError("La entrega reservada ya no está disponible.")
            model.reserva_id = None
            if delivered:
                model.estado = "entregada"
                model.entregada_en = instant
                model.ultimo_error = None
                result = "entregada"
            else:
                model.estado = "fallida" if model.intentos >= max_attempts else "pendiente"
                model.ultimo_error = (error or "entrega_fallida")[:80]
                model.proximo_intento_en = instant + timedelta(minutes=2**model.intentos)
                result = model.estado
            session.add(
                DeliveryAttemptModel(
                    intento_id=uuid4(),
                    notificacion_id=notification_id,
                    intentado_en=instant,
                    resultado=result,
                    error=model.ultimo_error,
                )
            )

    def list_notifications(self, client_id: UUID) -> list[dict[str, object]]:
        with Session(self._engine) as session:
            models = session.scalars(
                select(NotificationModel)
                .where(
                    NotificationModel.cliente_id == client_id,
                    NotificationModel.canal == "in_app",
                    NotificationModel.estado == "entregada",
                )
                .order_by(NotificationModel.entregada_en.desc())
                .limit(50)
            ).all()
            return [
                {
                    "notificacionId": str(model.notificacion_id),
                    "tipo": model.tipo,
                    "titulo": model.titulo,
                    "contenido": model.contenido,
                    "recurso": {
                        "tipo": model.recurso_tipo,
                        "id": str(model.recurso_id),
                    },
                    "entregadaEn": _iso(model.entregada_en),
                }
                for model in models
            ]

    def preferences(self, client_id: UUID) -> list[dict[str, object]]:
        with Session(self._engine) as session:
            stored = {
                (model.tipo, model.canal): model.habilitada
                for model in session.scalars(
                    select(NotificationPreferenceModel).where(
                        NotificationPreferenceModel.cliente_id == client_id
                    )
                )
            }
        return [
            {
                "tipo": notification_type,
                "canal": channel,
                "habilitada": stored.get((notification_type, channel), channel == "in_app"),
            }
            for notification_type in TYPES
            for channel in CHANNELS
        ]

    def update_preferences(
        self, client_id: UUID, preferences: list[Preference], now: datetime
    ) -> None:
        instant = _utc(now)
        with Session(self._engine) as session, session.begin():
            for preference in preferences:
                model = session.get(
                    NotificationPreferenceModel,
                    (client_id, preference.type, preference.channel),
                )
                if model is None:
                    model = NotificationPreferenceModel(
                        cliente_id=client_id,
                        tipo=preference.type,
                        canal=preference.channel,
                        habilitada=preference.enabled,
                        actualizada_en=instant,
                    )
                    session.add(model)
                else:
                    model.habilitada = preference.enabled
                    model.actualizada_en = instant

    @staticmethod
    def _enabled_channels(
        session: Session, client_id: UUID, notification_type: NotificationType
    ) -> tuple[Channel, ...]:
        stored = {
            model.canal: model.habilitada
            for model in session.scalars(
                select(NotificationPreferenceModel).where(
                    NotificationPreferenceModel.cliente_id == client_id,
                    NotificationPreferenceModel.tipo == notification_type,
                )
            )
        }
        return tuple(channel for channel in CHANNELS if stored.get(channel, channel == "in_app"))


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _iso(value: datetime | None) -> str:
    if value is None:
        raise RuntimeError("La notificación entregada no tiene fecha.")
    return _utc(value).isoformat().replace("+00:00", "Z")


def _notification_type(value: str) -> NotificationType:
    if value not in TYPES:
        raise RuntimeError("Tipo de notificación persistido inválido.")
    return cast(NotificationType, value)


def _channel(value: str) -> Channel:
    if value not in CHANNELS:
        raise RuntimeError("Canal persistido inválido.")
    return cast(Channel, value)


def _resource_type(value: str) -> ResourceType:
    if value not in {"pago", "siniestro", "poliza"}:
        raise RuntimeError("Recurso persistido inválido.")
    return cast(ResourceType, value)
