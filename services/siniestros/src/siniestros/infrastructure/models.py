from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class SiniestroModel(Base):
    __tablename__ = "siniestros"

    siniestro_id: Mapped[UUID] = mapped_column(primary_key=True)
    cliente_id: Mapped[UUID] = mapped_column(index=True)
    poliza_id: Mapped[UUID] = mapped_column(index=True)
    tipo: Mapped[str] = mapped_column(String(20))
    estado: Mapped[str] = mapped_column(String(30))
    ocurrido_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reportado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actualizado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CambioEstadoModel(Base):
    __tablename__ = "historial_estados_siniestro"

    siniestro_id: Mapped[UUID] = mapped_column(
        ForeignKey("siniestros.siniestro_id"), primary_key=True
    )
    secuencia: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    estado: Mapped[str] = mapped_column(String(30))
    ocurrido_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    visible_cliente: Mapped[bool]
