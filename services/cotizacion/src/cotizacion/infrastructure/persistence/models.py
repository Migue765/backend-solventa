from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    false,
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
    instantanea: Mapped["InstantaneaDecisionModel | None"] = relationship(lazy="selectin")


class OperacionSocioModel(Base):
    __tablename__ = "operaciones_socios"
    __table_args__ = (UniqueConstraint("operation_id", name="uq_operaciones_socios_operation"),)

    socio_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    operation_id: Mapped[UUID] = mapped_column(primary_key=True)
    usuario_id: Mapped[UUID]
    cliente_id: Mapped[UUID] = mapped_column(index=True)
    producto_id: Mapped[str] = mapped_column(String(64))
    solicitud_id: Mapped[str] = mapped_column(
        ForeignKey("cotizaciones.solicitud_id", ondelete="RESTRICT")
    )
    cotizacion_id: Mapped[UUID] = mapped_column(
        ForeignKey("cotizaciones.cotizacion_id", ondelete="RESTRICT")
    )
    perfilamiento_referencia: Mapped[str] = mapped_column(String(64))
    fingerprint: Mapped[str] = mapped_column(String(64))
    creada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OfertaSocioModel(Base):
    """Registro append-only: la oferta de una operación se inserta una vez y nunca se actualiza."""

    __tablename__ = "ofertas_socios"
    __table_args__ = (
        UniqueConstraint("socio_id", "operation_id", name="uq_ofertas_socios_operacion"),
        ForeignKeyConstraint(
            ["socio_id", "operation_id"],
            ["operaciones_socios.socio_id", "operaciones_socios.operation_id"],
            ondelete="RESTRICT",
        ),
    )

    oferta_socio_id: Mapped[UUID] = mapped_column(primary_key=True)
    socio_id: Mapped[str] = mapped_column(String(50))
    operation_id: Mapped[UUID]
    cliente_id: Mapped[UUID] = mapped_column(index=True)
    producto_id: Mapped[str] = mapped_column(String(64))
    solicitud_id: Mapped[str] = mapped_column(
        ForeignKey("cotizaciones.solicitud_id", ondelete="RESTRICT")
    )
    cotizacion_id: Mapped[UUID] = mapped_column(
        ForeignKey("cotizaciones.cotizacion_id", ondelete="RESTRICT")
    )
    hash_decision: Mapped[str] = mapped_column(String(64))
    hash_contenido: Mapped[str] = mapped_column(String(64))
    contenido: Mapped[dict[str, Any]] = mapped_column(JSON)
    calculada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    vigente_hasta: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OfertaHipotecariaModel(Base):
    __tablename__ = "ofertas_hipotecarias"
    __table_args__ = (
        ForeignKeyConstraint(
            ["socio_id", "operation_id"],
            ["operaciones_socios.socio_id", "operaciones_socios.operation_id"],
            ondelete="CASCADE",
        ),
    )

    socio_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    operation_id: Mapped[UUID] = mapped_column(primary_key=True)
    oferta_id: Mapped[UUID] = mapped_column(unique=True)
    version: Mapped[int] = mapped_column(Integer)
    contenido: Mapped[dict[str, Any]] = mapped_column(JSON)
    evidencia_decision: Mapped[dict[str, Any]] = mapped_column(JSON)
    hash_oferta: Mapped[str] = mapped_column(String(64), index=True)
    hash_decision: Mapped[str] = mapped_column(String(64), index=True)
    creada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AdmisionSocioModel(Base):
    __tablename__ = "admision_socios"

    socio_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    ventana: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    usadas: Mapped[int] = mapped_column(Integer)
    cuota_por_minuto: Mapped[int | None] = mapped_column(Integer)
    max_concurrentes: Mapped[int | None] = mapped_column(Integer)


class ReservaOperacionModel(Base):
    __tablename__ = "reservas_operaciones"
    __table_args__ = (UniqueConstraint("operation_global", name="uq_reservas_operation_global"),)

    socio_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    operation_id: Mapped[UUID] = mapped_column(primary_key=True)
    operation_global: Mapped[UUID | None]
    fingerprint: Mapped[str] = mapped_column(String(64))
    token: Mapped[UUID | None]
    vence_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class OperacionHipotecariaModel(Base):
    __tablename__ = "operaciones_hipotecarias"
    __table_args__ = (
        UniqueConstraint("socio_id", "operation_id", name="uq_operacion_hipotecaria_socio"),
    )

    operation_id: Mapped[UUID] = mapped_column(primary_key=True)
    socio_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    asesor_id: Mapped[UUID] = mapped_column(index=True)
    cliente_id: Mapped[UUID] = mapped_column(index=True)
    consentimiento_id: Mapped[UUID]
    consentimiento_version: Mapped[int] = mapped_column(Integer)
    referencia_credito: Mapped[str] = mapped_column(String(30))
    monto_credito: Mapped[Decimal] = mapped_column(MONTO)
    plazo_meses: Mapped[int] = mapped_column(Integer)
    solicitud_id: Mapped[str] = mapped_column(
        ForeignKey("cotizaciones.solicitud_id", ondelete="RESTRICT")
    )
    cotizacion_id: Mapped[UUID] = mapped_column(
        ForeignKey("cotizaciones.cotizacion_id", ondelete="RESTRICT")
    )
    perfilamiento_referencia: Mapped[str] = mapped_column(String(64))
    estado: Mapped[str] = mapped_column(String(20))
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    creada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))


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
    prima_mensual: Mapped[Decimal] = mapped_column(MONTO)
    periodicidad: Mapped[str] = mapped_column(
        String(20), default="mensual", server_default="mensual"
    )
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


class SuscripcionModel(Base):
    __tablename__ = "suscripciones"
    __table_args__ = (
        UniqueConstraint(
            "cliente_id",
            "idempotency_key",
            name="uq_suscripciones_cliente_idempotencia",
        ),
    )

    suscripcion_id: Mapped[UUID] = mapped_column(primary_key=True)
    solicitud_id: Mapped[str] = mapped_column(String(32), unique=True)
    cotizacion_id: Mapped[UUID] = mapped_column(
        ForeignKey("cotizaciones.cotizacion_id", ondelete="RESTRICT"),
        unique=True,
    )
    cliente_id: Mapped[UUID] = mapped_column(index=True)
    oferta_id: Mapped[UUID]
    oferta_version: Mapped[int] = mapped_column(Integer)
    estado: Mapped[str] = mapped_column(String(30))
    regla_version: Mapped[str] = mapped_column(String(40))
    creada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actualizada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[UUID]
    request_fingerprint: Mapped[str] = mapped_column(String(64))


class DecisionSuscripcionModel(Base):
    __tablename__ = "decisiones_suscripcion"

    suscripcion_id: Mapped[UUID] = mapped_column(
        ForeignKey("suscripciones.suscripcion_id", ondelete="CASCADE"),
        primary_key=True,
    )
    regla_version: Mapped[str] = mapped_column(String(40))
    resultado: Mapped[str] = mapped_column(String(30))
    datos_evaluados: Mapped[dict[str, Any]] = mapped_column(JSON)
    hash_decision: Mapped[str] = mapped_column(String(64), index=True)
    decidida_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SagaEmisionModel(Base):
    __tablename__ = "sagas_emision"

    suscripcion_id: Mapped[UUID] = mapped_column(
        ForeignKey("suscripciones.suscripcion_id", ondelete="CASCADE"),
        primary_key=True,
    )
    estado: Mapped[str] = mapped_column(String(30))
    version: Mapped[int] = mapped_column(Integer)
    actualizada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ultimo_error: Mapped[str | None] = mapped_column(String(500))


class PagoSimuladoModel(Base):
    __tablename__ = "pagos_simulados"
    __table_args__ = (
        UniqueConstraint("cliente_id", "idempotency_key", name="uq_pagos_simulados_idempotencia"),
    )

    pago_id: Mapped[UUID] = mapped_column(primary_key=True)
    solicitud_id: Mapped[str] = mapped_column(String(32), unique=True)
    suscripcion_id: Mapped[UUID] = mapped_column(
        ForeignKey("suscripciones.suscripcion_id", ondelete="RESTRICT"), unique=True
    )
    cliente_id: Mapped[UUID] = mapped_column(index=True)
    idempotency_key: Mapped[UUID]
    oferta_id: Mapped[UUID]
    oferta_version: Mapped[int] = mapped_column(Integer)
    monto: Mapped[Decimal] = mapped_column(MONTO)
    moneda: Mapped[str] = mapped_column(String(3))
    periodicidad: Mapped[str] = mapped_column(String(20))
    confirmada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    saga_version: Mapped[int] = mapped_column(Integer)
    emision_solicitada: Mapped[bool] = mapped_column(default=False, server_default=false())
