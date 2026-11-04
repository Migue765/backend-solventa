from datetime import datetime
from uuid import UUID

from sqlalchemy import JSON, DateTime, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class SiniestroModel(Base):
    __tablename__ = "siniestros"
    __table_args__ = (UniqueConstraint("cliente_id", "clave_idempotencia"),)

    siniestro_id: Mapped[UUID] = mapped_column(primary_key=True)
    cliente_id: Mapped[UUID] = mapped_column(index=True)
    poliza_id: Mapped[UUID] = mapped_column(index=True)
    clave_idempotencia: Mapped[UUID]
    contenido_hash: Mapped[str] = mapped_column(String(64))
    fecha_ocurrencia: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    tipo_evento: Mapped[str] = mapped_column(String(100))
    descripcion: Mapped[str] = mapped_column(String(5000))
    lugar: Mapped[str] = mapped_column(String(300))
    reportado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    estado: Mapped[str] = mapped_column(String(30))
    historial: Mapped[list[dict[str, str]]] = mapped_column(JSON)
