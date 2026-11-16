"""HU26: reglas versionadas y trazabilidad de evaluaciones automaticas."""

import sqlalchemy as sa
from alembic import op

revision = "0007_evaluacion_siniestros"
down_revision = "0006_decisiones_siniestro"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reglas_evaluacion_siniestros",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("huella", sa.String(64), nullable=False),
        sa.Column("definicion", sa.JSON(), nullable=False),
        sa.Column("vigente_desde", sa.DateTime(timezone=True), nullable=False),
        sa.Column("vigente_hasta", sa.DateTime(timezone=True), nullable=True),
        sa.Column("creada_en", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version > 0"),
        sa.UniqueConstraint("version"),
    )
    op.create_index(
        "ix_reglas_evaluacion_vigencia",
        "reglas_evaluacion_siniestros",
        ["vigente_desde", "version"],
    )
    op.create_table(
        "evaluaciones_siniestros",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "siniestro_id",
            sa.String(36),
            sa.ForeignKey("siniestros.id"),
            nullable=False,
        ),
        sa.Column("huella", sa.String(64), nullable=False),
        sa.Column("reglas_version", sa.Integer(), nullable=True),
        sa.Column("resultado", sa.String(30), nullable=False),
        sa.Column("tratamiento", sa.String(30), nullable=True),
        sa.Column("requiere_revision_humana", sa.Boolean(), nullable=False),
        sa.Column("entradas", sa.JSON(), nullable=False),
        sa.Column("reglas_aplicadas", sa.JSON(), nullable=False),
        sa.Column("motivo", sa.Text(), nullable=False),
        sa.Column("estado_anterior", sa.String(30), nullable=False),
        sa.Column("estado_resultante", sa.String(30), nullable=False),
        sa.Column("cantidad_evidencias", sa.Integer(), nullable=False),
        sa.Column("evaluada_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("correlacion", sa.String(36), nullable=False),
        sa.CheckConstraint(
            "resultado IN ('decidido', 'informacion_insuficiente', 'pendiente_revision')"
        ),
        sa.CheckConstraint(
            "tratamiento IS NULL OR tratamiento IN "
            "('aprobacion', 'inspeccion', 'rechazo', 'revision_humana')"
        ),
        sa.UniqueConstraint("siniestro_id", "huella"),
    )
    op.create_index(
        "ix_evaluaciones_siniestro_fecha",
        "evaluaciones_siniestros",
        ["siniestro_id", "evaluada_en"],
    )


def downgrade() -> None:
    op.drop_index("ix_evaluaciones_siniestro_fecha", table_name="evaluaciones_siniestros")
    op.drop_table("evaluaciones_siniestros")
    op.drop_index("ix_reglas_evaluacion_vigencia", table_name="reglas_evaluacion_siniestros")
    op.drop_table("reglas_evaluacion_siniestros")
