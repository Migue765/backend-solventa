from datetime import datetime
from typing import Any

from siniestros.models import Base
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column


class ReglasEvaluacionModel(Base):
    __tablename__ = "reglas_evaluacion_siniestros"
    __table_args__ = (
        CheckConstraint("version > 0"),
        UniqueConstraint("version"),
        Index("ix_reglas_evaluacion_vigencia", "vigente_desde", "version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    version: Mapped[int] = mapped_column(Integer)
    huella: Mapped[str] = mapped_column(String(64))
    definicion: Mapped[dict[str, Any]] = mapped_column(JSON)
    vigente_desde: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    vigente_hasta: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    creada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EvaluacionSiniestroModel(Base):
    __tablename__ = "evaluaciones_siniestros"
    __table_args__ = (
        UniqueConstraint("siniestro_id", "huella"),
        CheckConstraint(
            "resultado IN ('decidido', 'informacion_insuficiente', 'pendiente_revision')"
        ),
        CheckConstraint(
            "tratamiento IS NULL OR tratamiento IN "
            "('aprobacion', 'inspeccion', 'rechazo', 'revision_humana')"
        ),
        Index("ix_evaluaciones_siniestro_fecha", "siniestro_id", "evaluada_en"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    siniestro_id: Mapped[str] = mapped_column(ForeignKey("siniestros.id"))
    huella: Mapped[str] = mapped_column(String(64))
    reglas_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resultado: Mapped[str] = mapped_column(String(30))
    tratamiento: Mapped[str | None] = mapped_column(String(30), nullable=True)
    requiere_revision_humana: Mapped[bool] = mapped_column(Boolean)
    entradas: Mapped[dict[str, Any]] = mapped_column(JSON)
    reglas_aplicadas: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    motivo: Mapped[str] = mapped_column(Text)
    estado_anterior: Mapped[str] = mapped_column(String(30))
    estado_resultante: Mapped[str] = mapped_column(String(30))
    cantidad_evidencias: Mapped[int] = mapped_column(Integer)
    evaluada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    correlacion: Mapped[str] = mapped_column(String(36))
