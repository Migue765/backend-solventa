from datetime import date, datetime
from uuid import UUID

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, UniqueConstraint
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
    # activa o cancelada; la cancelacion es definitiva y conserva las fechas originales.
    estado: Mapped[str] = mapped_column(String(20), default="activa", server_default="activa")


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


class VersionPolizaModel(Base):
    """Instantanea historica de condiciones; nunca se actualiza ni se borra (D-09)."""

    __tablename__ = "versiones_poliza"

    poliza_id: Mapped[UUID] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(primary_key=True)
    plan: Mapped[str] = mapped_column(String(20))
    coberturas: Mapped[list[str]] = mapped_column(JSON)
    prima: Mapped[str] = mapped_column(String(20))
    efectivo_desde: Mapped[date]
    origen: Mapped[str] = mapped_column(String(20))
    endoso_id: Mapped[UUID | None]
    creada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EndosoModel(Base):
    __tablename__ = "endosos"
    __table_args__ = (UniqueConstraint("cliente_id", "idempotency_key"),)

    endoso_id: Mapped[UUID] = mapped_column(primary_key=True)
    poliza_id: Mapped[UUID] = mapped_column(index=True)
    cliente_id: Mapped[UUID]
    idempotency_key: Mapped[UUID]
    solicitud_hash: Mapped[str] = mapped_column(String(64))
    version_anterior: Mapped[int]
    version_nueva: Mapped[int]
    impacto: Mapped[dict[str, object]] = mapped_column(JSON)
    impacto_hash: Mapped[str] = mapped_column(String(64))
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CancelacionModel(Base):
    """Cancelacion definitiva; la unicidad por poliza impide registrar dos cancelaciones."""

    __tablename__ = "cancelaciones"
    __table_args__ = (UniqueConstraint("cliente_id", "idempotency_key"),)

    cancelacion_id: Mapped[UUID] = mapped_column(primary_key=True)
    poliza_id: Mapped[UUID] = mapped_column(unique=True)
    cliente_id: Mapped[UUID]
    idempotency_key: Mapped[UUID]
    solicitud_hash: Mapped[str] = mapped_column(String(64))
    motivo: Mapped[str] = mapped_column(String(40))
    liquidacion: Mapped[dict[str, object]] = mapped_column(JSON)
    liquidacion_hash: Mapped[str] = mapped_column(String(64))
    fecha_efectiva: Mapped[date]
    creada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuditoriaPolizaModel(Base):
    """Registro append-only de modificaciones y cancelaciones confirmadas y rechazadas."""

    __tablename__ = "auditoria_polizas"

    auditoria_id: Mapped[UUID] = mapped_column(primary_key=True)
    poliza_id: Mapped[UUID] = mapped_column(index=True)
    cliente_id: Mapped[UUID]
    accion: Mapped[str] = mapped_column(String(40))
    resultado: Mapped[str] = mapped_column(String(20))
    motivo: Mapped[str | None] = mapped_column(String(60))
    detalle: Mapped[dict[str, object]] = mapped_column(JSON)
    correlation_id: Mapped[str] = mapped_column(String(64))
    ocurrido_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DocumentoPolizaModel(Base):
    """Instantanea inmutable de los bytes consultados y descargables."""

    __tablename__ = "documentos_poliza"
    __table_args__ = (UniqueConstraint("poliza_id", "contenido_hash"),)

    documento_id: Mapped[UUID] = mapped_column(primary_key=True)
    poliza_id: Mapped[UUID] = mapped_column(ForeignKey("polizas.poliza_id"), index=True)
    version_poliza: Mapped[int | None]
    contenido_hash: Mapped[str] = mapped_column(String(64))
    contenido: Mapped[str] = mapped_column(Text)
    archivo: Mapped[str] = mapped_column(Text)
    creada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AceptacionDocumentoModel(Base):
    """Evidencia append-only; actor y fecha se obtienen del servidor."""

    __tablename__ = "aceptaciones_documento"
    __table_args__ = (UniqueConstraint("cliente_id", "idempotency_key"),)

    aceptacion_id: Mapped[UUID] = mapped_column(primary_key=True)
    documento_id: Mapped[UUID] = mapped_column(ForeignKey("documentos_poliza.documento_id"))
    poliza_id: Mapped[UUID] = mapped_column(ForeignKey("polizas.poliza_id"), index=True)
    cliente_id: Mapped[UUID]
    idempotency_key: Mapped[UUID]
    solicitud_hash: Mapped[str] = mapped_column(String(64))
    aceptada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
