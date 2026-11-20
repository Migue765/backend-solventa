from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
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
        CheckConstraint('"offset" >= 0 AND "offset" <= size'),
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


class ParametricQueueState(Base):
    __tablename__ = "estado_cola_parametrica"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pendientes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ParametricEvent(Base):
    __tablename__ = "eventos_parametricos"
    __table_args__ = (
        UniqueConstraint("fuente", "evento_id"),
        CheckConstraint("estado IN ('pendiente', 'procesado')"),
        Index("ix_eventos_parametricos_estado_recibido", "estado", "recibido_en"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    fuente: Mapped[str] = mapped_column(String(100))
    evento_id: Mapped[str] = mapped_column(String(36))
    fingerprint: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    ocurrido_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recibido_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    estado: Mapped[str] = mapped_column(String(20), default="pendiente")
    procesado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ParametricRule(Base):
    __tablename__ = "reglas_parametricas"
    __table_args__ = (
        CheckConstraint("version > 0"),
        CheckConstraint("operador IN ('gt', 'gte', 'lt', 'lte', 'eq')"),
        UniqueConstraint("regla_id", "version"),
        Index(
            "ix_reglas_parametricas_busqueda",
            "fuente",
            "tipo_evento",
            "metrica",
            "unidad",
            "vigente_desde",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    regla_id: Mapped[str] = mapped_column(String(36))
    version: Mapped[int] = mapped_column(Integer)
    fingerprint: Mapped[str] = mapped_column(String(64))
    fuente: Mapped[str] = mapped_column(String(100))
    tipo_evento: Mapped[str] = mapped_column(String(100))
    metrica: Mapped[str] = mapped_column(String(100))
    operador: Mapped[str] = mapped_column(String(8))
    umbral: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    unidad: Mapped[str] = mapped_column(String(30))
    ubicacion_codigo: Mapped[str | None] = mapped_column(String(100), nullable=True)
    cliente_id: Mapped[str] = mapped_column(String(36))
    poliza_id: Mapped[str] = mapped_column(String(36))
    beneficiario_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    monto_indemnizacion: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2),
        nullable=True,
    )
    moneda: Mapped[str | None] = mapped_column(String(3), nullable=True)
    vigente_desde: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    vigente_hasta: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    creada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ParametricDecision(Base):
    __tablename__ = "decisiones_parametricas"
    __table_args__ = (
        UniqueConstraint("evento_id", "regla_id", "regla_version"),
        CheckConstraint("decision IN ('valido', 'no_valido', 'sin_regla_vigente')"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    evento_id: Mapped[str] = mapped_column(ForeignKey("eventos_parametricos.id"), index=True)
    regla_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    regla_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decision: Mapped[str] = mapped_column(String(20))
    decidida_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    siniestro_id: Mapped[str | None] = mapped_column(ForeignKey("siniestros.id"), nullable=True)


class AutomaticPaymentCase(Base):
    __tablename__ = "casos_pago_automatico"
    __table_args__ = (
        UniqueConstraint("evento_id", "regla_id", "regla_version"),
        CheckConstraint("estado IN ('pendiente', 'entregado')"),
        Index("ix_casos_pago_estado_creado", "estado", "creado_en"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    evento_id: Mapped[str] = mapped_column(ForeignKey("eventos_parametricos.id"))
    regla_id: Mapped[str] = mapped_column(String(36))
    regla_version: Mapped[int] = mapped_column(Integer)
    siniestro_id: Mapped[str] = mapped_column(ForeignKey("siniestros.id"), unique=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    estado: Mapped[str] = mapped_column(String(20), default="pendiente")
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    entregado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rechazado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ultimo_error: Mapped[str | None] = mapped_column(String(100), nullable=True)


class ParametricPayment(Base):
    __tablename__ = "pagos_parametricos"
    __table_args__ = (
        CheckConstraint("estado IN ('pendiente', 'confirmado', 'rechazado')"),
        CheckConstraint("monto > 0"),
        UniqueConstraint("caso_id"),
        UniqueConstraint("siniestro_id"),
        Index("ix_pagos_parametricos_estado_proximo", "estado", "proximo_intento_en"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    caso_id: Mapped[str] = mapped_column(ForeignKey("casos_pago_automatico.id"))
    evento_id: Mapped[str] = mapped_column(ForeignKey("eventos_parametricos.id"))
    siniestro_id: Mapped[str] = mapped_column(ForeignKey("siniestros.id"))
    regla_id: Mapped[str] = mapped_column(String(36))
    regla_version: Mapped[int] = mapped_column(Integer)
    beneficiario_id: Mapped[str] = mapped_column(String(36))
    monto: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    moneda: Mapped[str] = mapped_column(String(3))
    huella: Mapped[str] = mapped_column(String(64))
    estado: Mapped[str] = mapped_column(String(20), default="pendiente")
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actualizado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finalizado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    proximo_intento_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_hasta: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    intentos: Mapped[int] = mapped_column(Integer, default=0)
    ultimo_error: Mapped[str | None] = mapped_column(String(100), nullable=True)


class ParametricPaymentAttempt(Base):
    __tablename__ = "intentos_pago_parametrico"
    __table_args__ = (
        CheckConstraint("numero > 0"),
        CheckConstraint("estado IN ('en_proceso', 'ambiguo', 'confirmado', 'rechazado')"),
        UniqueConstraint("pago_id", "numero"),
        Index("ix_intentos_pago_parametrico_pago", "pago_id", "numero"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    pago_id: Mapped[str] = mapped_column(ForeignKey("pagos_parametricos.id"))
    numero: Mapped[int] = mapped_column(Integer)
    estado: Mapped[str] = mapped_column(String(20))
    iniciado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finalizado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    codigo: Mapped[str | None] = mapped_column(String(100), nullable=True)


class ParametricPaymentEffect(Base):
    __tablename__ = "efectos_pago_parametrico"

    pago_id: Mapped[str] = mapped_column(
        ForeignKey("pagos_parametricos.id"),
        primary_key=True,
    )
    huella: Mapped[str] = mapped_column(String(64))
    resultado: Mapped[str] = mapped_column(String(20))
    evaluado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NotificationOutbox(Base):
    __tablename__ = "outbox_notificaciones"
    __table_args__ = (
        CheckConstraint("tipo IN ('siniestro_estado_cambiado', 'pago_parametrico_ejecutado')"),
        CheckConstraint("intentos >= 0"),
        Index("ix_outbox_notificaciones_pendientes", "publicado_en", "creado_en"),
    )

    evento_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tipo: Mapped[str] = mapped_column(String(50))
    cliente_id: Mapped[str] = mapped_column(String(36))
    recurso_tipo: Mapped[str] = mapped_column(String(20))
    recurso_id: Mapped[str] = mapped_column(String(36))
    estado: Mapped[str | None] = mapped_column(String(30), nullable=True)
    ocurrido_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    publicado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    intentos: Mapped[int] = mapped_column(Integer, default=0)
    ultimo_error: Mapped[str | None] = mapped_column(String(100), nullable=True)
