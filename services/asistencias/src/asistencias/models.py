from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Provider(Base):
    __tablename__ = "prestadores"
    __table_args__ = (Index("ix_prestadores_tipo_disponible", "tipo_servicio", "disponible"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    nombre: Mapped[str] = mapped_column(String(120))
    tipo_servicio: Mapped[str] = mapped_column(String(80))
    latitud: Mapped[Decimal] = mapped_column(Numeric(9, 6))
    longitud: Mapped[Decimal] = mapped_column(Numeric(9, 6))
    disponible: Mapped[bool] = mapped_column(Boolean)


class Assistance(Base):
    __tablename__ = "solicitudes_asistencia"
    __table_args__ = (
        UniqueConstraint("cliente_id", "operacion_id"),
        CheckConstraint("estado IN ('registrada')"),
        CheckConstraint(
            "(poliza_id IS NOT NULL AND siniestro_id IS NULL) OR "
            "(poliza_id IS NULL AND siniestro_id IS NOT NULL)"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    cliente_id: Mapped[str] = mapped_column(String(36))
    operacion_id: Mapped[str] = mapped_column(String(36))
    fingerprint: Mapped[str] = mapped_column(String(64))
    prestador_id: Mapped[str] = mapped_column(ForeignKey("prestadores.id"))
    tipo_asistencia: Mapped[str] = mapped_column(String(80))
    poliza_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    siniestro_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    latitud: Mapped[Decimal] = mapped_column(Numeric(9, 6))
    longitud: Mapped[Decimal] = mapped_column(Numeric(9, 6))
    origen_ubicacion: Mapped[str] = mapped_column(String(20))
    ubicacion_confirmada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    estado: Mapped[str] = mapped_column(String(20))
    creada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class LocationAudit(Base):
    __tablename__ = "auditoria_ubicacion"
    __table_args__ = (
        Index("ix_auditoria_ubicacion_cliente_fecha", "cliente_id", "usada_en"),
        CheckConstraint("proposito IN ('consulta_prestadores', 'registro_asistencia')"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    cliente_id: Mapped[str] = mapped_column(String(36))
    proposito: Mapped[str] = mapped_column(String(40))
    correlation_id: Mapped[str] = mapped_column(String(36))
    asistencia_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    usada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
