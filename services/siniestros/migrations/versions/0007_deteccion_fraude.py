"""Agrega reglas versionadas, evaluaciones y cola de revision del motor de fraude."""

import sqlalchemy as sa
from alembic import op

revision = "0007_deteccion_fraude"
down_revision = "0006_decisiones_siniestro"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reglas_fraude",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("regla_id", sa.String(36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("nombre", sa.String(100), nullable=False),
        sa.Column("motivo", sa.String(300), nullable=False),
        sa.Column("senal", sa.String(60), nullable=False),
        sa.Column("operador", sa.String(8), nullable=False),
        sa.Column("umbral", sa.Numeric(18, 6), nullable=False),
        sa.Column("peso", sa.Integer(), nullable=False),
        sa.Column("vigente_desde", sa.DateTime(timezone=True), nullable=False),
        sa.Column("vigente_hasta", sa.DateTime(timezone=True), nullable=True),
        sa.Column("creada_en", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version > 0"),
        sa.CheckConstraint("peso >= 1 AND peso <= 100"),
        sa.CheckConstraint("operador IN ('gt', 'gte', 'lt', 'lte', 'eq')"),
        sa.UniqueConstraint("regla_id", "version"),
    )
    op.create_table(
        "evaluaciones_fraude",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("siniestro_id", sa.String(36), sa.ForeignKey("siniestros.id"), nullable=False),
        sa.Column("huella", sa.String(64), nullable=True),
        sa.Column("resultado", sa.String(20), nullable=False),
        sa.Column("accion", sa.String(20), nullable=False),
        sa.Column("puntaje", sa.Integer(), nullable=False),
        sa.Column("prioridad", sa.String(10), nullable=True),
        sa.Column("motivo", sa.String(2000), nullable=False),
        sa.Column("senales", sa.JSON(), nullable=False),
        sa.Column("evidencia", sa.JSON(), nullable=False),
        sa.Column("reglas", sa.JSON(), nullable=False),
        sa.Column("version_motor", sa.String(20), nullable=False),
        sa.Column("politica", sa.String(20), nullable=True),
        sa.Column("incidente", sa.String(100), nullable=True),
        sa.Column("duracion_ms", sa.Integer(), nullable=False),
        sa.Column("origen", sa.String(20), nullable=False),
        sa.Column("correlation", sa.String(36), nullable=False),
        sa.Column("evaluada_en", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("resultado IN ('sospechoso', 'legitimo', 'indeterminado')"),
        sa.CheckConstraint("accion IN ('revision_humana', 'continuar')"),
    )
    op.create_index(
        "ix_evaluaciones_fraude_siniestro",
        "evaluaciones_fraude",
        ["siniestro_id", "evaluada_en"],
    )
    op.create_table(
        "casos_revision_fraude",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "siniestro_id",
            sa.String(36),
            sa.ForeignKey("siniestros.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "evaluacion_id",
            sa.String(36),
            sa.ForeignKey("evaluaciones_fraude.id"),
            nullable=False,
        ),
        sa.Column("prioridad", sa.String(10), nullable=False),
        sa.Column("rango_prioridad", sa.Integer(), nullable=False),
        sa.Column("puntaje", sa.Integer(), nullable=False),
        sa.Column("estado", sa.String(20), nullable=False),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actualizado_en", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("prioridad IN ('alta', 'media', 'baja')"),
        sa.CheckConstraint("estado IN ('pendiente', 'resuelto')"),
    )
    op.create_index(
        "ix_casos_revision_fraude_cola",
        "casos_revision_fraude",
        ["estado", "rango_prioridad", "creado_en"],
    )


def downgrade() -> None:
    op.drop_index("ix_casos_revision_fraude_cola", table_name="casos_revision_fraude")
    op.drop_table("casos_revision_fraude")
    op.drop_index("ix_evaluaciones_fraude_siniestro", table_name="evaluaciones_fraude")
    op.drop_table("evaluaciones_fraude")
    op.drop_table("reglas_fraude")
