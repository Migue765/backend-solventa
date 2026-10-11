from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint, func
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
    identificacion: Mapped[str | None] = mapped_column(String(20))
    patente_vehiculo: Mapped[str | None] = mapped_column(String(8))
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
