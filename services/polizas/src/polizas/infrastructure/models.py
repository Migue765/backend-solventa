from datetime import date, datetime
from uuid import UUID

from sqlalchemy import DateTime, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class PolizaModel(Base):
    __tablename__ = "polizas"

    poliza_id: Mapped[UUID] = mapped_column(primary_key=True)
    cliente_id: Mapped[UUID] = mapped_column(index=True)
    suscripcion_id: Mapped[UUID] = mapped_column(unique=True)
    pago_id: Mapped[UUID] = mapped_column(unique=True)
    tipo: Mapped[str] = mapped_column(String(30))
    fecha_inicio: Mapped[date]
    fecha_fin: Mapped[date]
    emitida_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    evidencia_hash: Mapped[str] = mapped_column(String(64))
    regla_version: Mapped[str] = mapped_column(String(40))
