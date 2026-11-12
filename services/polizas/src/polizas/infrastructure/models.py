from datetime import date, datetime
from uuid import UUID

from sqlalchemy import JSON, DateTime, String, UniqueConstraint
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


class RenovacionModel(Base):
    __tablename__ = "renovaciones"
    __table_args__ = (UniqueConstraint("cliente_id", "idempotency_key"),)

    renovacion_id: Mapped[UUID] = mapped_column(primary_key=True)
    poliza_id: Mapped[UUID] = mapped_column(index=True)
    cliente_id: Mapped[UUID]
    idempotency_key: Mapped[UUID]
    solicitud_hash: Mapped[str] = mapped_column(String(64))
    # Una sola renovacion activa o exitosa por periodo; se libera si el pago es rechazado.
    reserva_periodo: Mapped[str | None] = mapped_column(String(80), unique=True)
    fin_anterior: Mapped[date]
    nuevo_inicio: Mapped[date]
    nuevo_fin: Mapped[date]
    condiciones: Mapped[dict[str, object]] = mapped_column(JSON)
    condiciones_hash: Mapped[str] = mapped_column(String(64))
    regla_version: Mapped[str] = mapped_column(String(40))
    estado: Mapped[str] = mapped_column(String(20))
    motivo: Mapped[str | None] = mapped_column(String(40))
    intentos_pago: Mapped[int]
    creada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actualizada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PagoRenovacionModel(Base):
    """Efecto del pago simulado: la clave primaria garantiza un solo cobro por renovacion."""

    __tablename__ = "pagos_renovacion"

    renovacion_id: Mapped[UUID] = mapped_column(primary_key=True)
    monto: Mapped[str] = mapped_column(String(20))
    moneda: Mapped[str] = mapped_column(String(3))
    resultado: Mapped[str] = mapped_column(String(20))
    evaluado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
