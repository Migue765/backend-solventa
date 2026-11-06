from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
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


class Claim(Base):
    __tablename__ = "siniestros"
    __table_args__ = (UniqueConstraint("owner", "operation"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner: Mapped[str] = mapped_column(String(36), index=True)
    operation: Mapped[str] = mapped_column(String(36))
    fingerprint: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created: Mapped[str] = mapped_column(String(40))
    tipo: Mapped[str | None] = mapped_column(String(20), nullable=True)
    estado: Mapped[str] = mapped_column(String(30), default="reportado", server_default="reportado")
    actualizado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Upload(Base):
    __tablename__ = "cargas_siniestros"
    __table_args__ = (
        UniqueConstraint("owner", "operation"),
        CheckConstraint("offset >= 0 AND offset <= size"),
        CheckConstraint("status IN ('pendiente', 'confirmada', 'cancelada')"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    claim_id: Mapped[str] = mapped_column(ForeignKey("siniestros.id"), index=True)
    owner: Mapped[str] = mapped_column(String(36))
    operation: Mapped[str] = mapped_column(String(36))
    fingerprint: Mapped[str] = mapped_column(String(64))
    mime: Mapped[str] = mapped_column(String(40))
    size: Mapped[int] = mapped_column(Integer)
    digest: Mapped[str] = mapped_column(String(64))
    location: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    captured: Mapped[str | None] = mapped_column(String(40), nullable=True)
    offset: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="pendiente")
    created: Mapped[str] = mapped_column(String(40))
    confirmed: Mapped[str | None] = mapped_column(String(40), nullable=True)


class Audit(Base):
    __tablename__ = "auditoria_siniestros"
    __table_args__ = (Index("ix_auditoria_operacion", "actor", "action", "operation", "created"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    actor: Mapped[str | None] = mapped_column(String(36), nullable=True)
    action: Mapped[str] = mapped_column(String(64))
    result: Mapped[str] = mapped_column(String(64))
    correlation: Mapped[str] = mapped_column(String(36))
    created: Mapped[str] = mapped_column(String(40))
    operation: Mapped[str | None] = mapped_column(String(36), nullable=True)
    fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
