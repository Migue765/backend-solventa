from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class NotificationEventModel(Base):
    __tablename__ = "eventos_notificacion"

    evento_id: Mapped[UUID] = mapped_column(primary_key=True)
    tipo: Mapped[str] = mapped_column(String(50))
    cliente_id: Mapped[UUID] = mapped_column(index=True)
    ocurrido_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recibido_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    huella: Mapped[str] = mapped_column(String(64))
    resultado: Mapped[str] = mapped_column(String(20))


class NotificationPreferenceModel(Base):
    __tablename__ = "preferencias_notificacion"

    cliente_id: Mapped[UUID] = mapped_column(primary_key=True)
    tipo: Mapped[str] = mapped_column(String(30), primary_key=True)
    canal: Mapped[str] = mapped_column(String(20), primary_key=True)
    habilitada: Mapped[bool] = mapped_column(Boolean)
    actualizada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NotificationModel(Base):
    __tablename__ = "notificaciones"
    __table_args__ = (
        UniqueConstraint("evento_id", "tipo", "canal", name="uq_notificacion_evento_tipo_canal"),
        CheckConstraint("intentos >= 0", name="ck_notificacion_intentos"),
        Index("ix_notificaciones_cliente_entrega", "cliente_id", "canal", "entregada_en"),
        Index("ix_notificaciones_pendientes", "estado", "proximo_intento_en"),
    )

    notificacion_id: Mapped[UUID] = mapped_column(primary_key=True)
    evento_id: Mapped[UUID] = mapped_column(
        ForeignKey("eventos_notificacion.evento_id", ondelete="RESTRICT")
    )
    cliente_id: Mapped[UUID]
    tipo: Mapped[str] = mapped_column(String(30))
    canal: Mapped[str] = mapped_column(String(20))
    titulo: Mapped[str] = mapped_column(String(80))
    contenido: Mapped[str] = mapped_column(String(240))
    recurso_tipo: Mapped[str] = mapped_column(String(20))
    recurso_id: Mapped[UUID]
    programada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    estado: Mapped[str] = mapped_column(String(20))
    intentos: Mapped[int] = mapped_column(Integer)
    proximo_intento_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    entregada_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ultimo_error: Mapped[str | None] = mapped_column(String(80))
    reserva_id: Mapped[UUID | None]
    creada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DeliveryAttemptModel(Base):
    __tablename__ = "intentos_entrega_notificacion"

    intento_id: Mapped[UUID] = mapped_column(primary_key=True)
    notificacion_id: Mapped[UUID] = mapped_column(
        ForeignKey("notificaciones.notificacion_id", ondelete="RESTRICT"), index=True
    )
    intentado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resultado: Mapped[str] = mapped_column(String(20))
    error: Mapped[str | None] = mapped_column(String(80))
