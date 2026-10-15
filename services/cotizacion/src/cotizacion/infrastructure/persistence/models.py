from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

MONTO = Numeric(18, 2)


class Base(DeclarativeBase):
    pass


class CotizacionModel(Base):
    __tablename__ = "cotizaciones"
    __table_args__ = (
        UniqueConstraint(
            "cliente_id",
            "idempotency_key",
            name="uq_cotizaciones_cliente_idempotencia",
        ),
    )

    cotizacion_id: Mapped[UUID] = mapped_column(primary_key=True)
    cliente_id: Mapped[UUID] = mapped_column(index=True)
    producto_id: Mapped[UUID]
    estado: Mapped[str] = mapped_column(String(20))
    moneda: Mapped[str] = mapped_column(String(3))
    prima_estimada: Mapped[Decimal] = mapped_column(MONTO)
    valida_hasta: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    creada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    solicitud_id: Mapped[str | None] = mapped_column(String(32), unique=True)
    tipo_cliente: Mapped[str | None] = mapped_column(String(30))
    tipo_documento: Mapped[str | None] = mapped_column(String(20))
    identificacion: Mapped[str | None] = mapped_column(String(20))
    datos_producto: Mapped[dict[str, str] | None] = mapped_column(JSON)
    correo: Mapped[str | None] = mapped_column(String(254))
    telefono: Mapped[str | None] = mapped_column(String(16))
    idempotency_key: Mapped[UUID | None]
    request_fingerprint: Mapped[str | None] = mapped_column(String(64))

    coberturas: Mapped[list["CoberturaCotizadaModel"]] = relationship(
        back_populates="cotizacion",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="CoberturaCotizadaModel.orden",
    )


class CoberturaCotizadaModel(Base):
    __tablename__ = "coberturas_cotizadas"

    cotizacion_id: Mapped[UUID] = mapped_column(
        ForeignKey("cotizaciones.cotizacion_id", ondelete="CASCADE"), primary_key=True
    )
    cobertura_id: Mapped[UUID] = mapped_column(primary_key=True)
    suma_asegurada: Mapped[Decimal] = mapped_column(MONTO)
    deducible: Mapped[Decimal] = mapped_column(MONTO)
    prima: Mapped[Decimal] = mapped_column(MONTO)
    orden: Mapped[int] = mapped_column(Integer)

    cotizacion: Mapped[CotizacionModel] = relationship(back_populates="coberturas")


class SeleccionOfertaModel(Base):
    __tablename__ = "selecciones_oferta"

    cotizacion_id: Mapped[UUID] = mapped_column(
        ForeignKey("cotizaciones.cotizacion_id", ondelete="CASCADE"),
        primary_key=True,
    )
    solicitud_id: Mapped[str] = mapped_column(String(32), unique=True)
    oferta_id: Mapped[UUID]
    version: Mapped[int] = mapped_column(Integer)
    nombre_oferta: Mapped[str] = mapped_column(String(120))
    prima_mensual: Mapped[Decimal] = mapped_column(Numeric(10, 4))
    moneda: Mapped[str] = mapped_column(String(3))
    seleccionada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[UUID]
    request_fingerprint: Mapped[str] = mapped_column(String(64))


class ReglaTarifaModel(Base):
    __tablename__ = "reglas_tarifa"
    __table_args__ = (
        UniqueConstraint("producto_id", "version", name="uq_reglas_producto_version"),
    )

    regla_id: Mapped[UUID] = mapped_column(primary_key=True)
    version: Mapped[str] = mapped_column(String(40))
    producto_id: Mapped[UUID] = mapped_column(index=True)
    tasa: Mapped[Decimal] = mapped_column(Numeric(10, 6))
    factor_perfil: Mapped[Decimal] = mapped_column(Numeric(10, 6))
    vigente_desde: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    vigente_hasta: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InstantaneaDecisionModel(Base):
    """Registro append-only: se inserta junto con la cotización y nunca se actualiza."""

    __tablename__ = "instantaneas_decision"

    cotizacion_id: Mapped[UUID] = mapped_column(
        ForeignKey("cotizaciones.cotizacion_id", ondelete="CASCADE"), primary_key=True
    )
    regla_id: Mapped[UUID]
    regla_version: Mapped[str] = mapped_column(String(40))
    perfil_id: Mapped[str] = mapped_column(String(64))
    perfil_modelo_version: Mapped[str] = mapped_column(String(40))
    hash_decision: Mapped[str] = mapped_column(String(64), index=True)
    hash_contenido: Mapped[str] = mapped_column(String(64))
    contenido: Mapped[dict[str, Any]] = mapped_column(JSON)
    creada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OutboxEventoModel(Base):
    """Outbox transaccional: el evento se confirma en la misma transacción que la decisión."""

    __tablename__ = "outbox_eventos"

    evento_id: Mapped[UUID] = mapped_column(primary_key=True)
    tipo: Mapped[str] = mapped_column(String(80))
    agregado_id: Mapped[UUID] = mapped_column(index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    publicado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    intentos: Mapped[int] = mapped_column(Integer, default=0)
