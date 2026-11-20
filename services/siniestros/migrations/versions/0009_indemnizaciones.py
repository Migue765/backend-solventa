"""Agrega indemnizaciones durables de siniestros aprobados, intentos y efectos."""

import sqlalchemy as sa
from alembic import op

revision = "0009_indemnizaciones"
down_revision = "0008_outbox_notificaciones"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "indemnizaciones",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "siniestro_id",
            sa.String(36),
            sa.ForeignKey("siniestros.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("operacion", sa.String(36), nullable=False, unique=True),
        sa.Column("huella_solicitud", sa.String(64), nullable=False),
        sa.Column("huella", sa.String(64), nullable=False),
        sa.Column("cliente_id", sa.String(36), nullable=False),
        sa.Column("poliza_id", sa.String(36), nullable=False),
        sa.Column("beneficiario_id", sa.String(36), nullable=False),
        sa.Column("monto", sa.Numeric(18, 2), nullable=False),
        sa.Column("moneda", sa.String(3), nullable=False),
        sa.Column("estado", sa.String(20), nullable=False),
        sa.Column("referencia", sa.String(100), nullable=True),
        sa.Column("correlacion", sa.String(36), nullable=False),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actualizado_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finalizado_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("proximo_intento_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_token", sa.String(36), nullable=True),
        sa.Column("lease_hasta", sa.DateTime(timezone=True), nullable=True),
        sa.Column("intentos", sa.Integer(), nullable=False),
        sa.Column("ultimo_error", sa.String(100), nullable=True),
        sa.CheckConstraint("estado IN ('pendiente', 'confirmado', 'rechazado')"),
        sa.CheckConstraint("monto > 0"),
        sa.CheckConstraint("intentos >= 0"),
    )
    op.create_index(
        "ix_indemnizaciones_estado_proximo",
        "indemnizaciones",
        ["estado", "proximo_intento_en"],
    )
    op.create_table(
        "intentos_indemnizacion",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "indemnizacion_id",
            sa.String(36),
            sa.ForeignKey("indemnizaciones.id"),
            nullable=False,
        ),
        sa.Column("numero", sa.Integer(), nullable=False),
        sa.Column("estado", sa.String(20), nullable=False),
        sa.Column("iniciado_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finalizado_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("codigo", sa.String(100), nullable=True),
        sa.Column("referencia", sa.String(100), nullable=True),
        sa.CheckConstraint("numero > 0"),
        sa.CheckConstraint("estado IN ('en_proceso', 'ambiguo', 'confirmado', 'rechazado')"),
        sa.UniqueConstraint("indemnizacion_id", "numero"),
    )
    op.create_table(
        "efectos_indemnizacion",
        sa.Column(
            "indemnizacion_id",
            sa.String(36),
            sa.ForeignKey("indemnizaciones.id"),
            primary_key=True,
        ),
        sa.Column("huella", sa.String(64), nullable=False),
        sa.Column("resultado", sa.String(20), nullable=False),
        sa.Column("referencia", sa.String(100), nullable=False),
        sa.Column("evaluado_en", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("resultado IN ('confirmado', 'rechazado')"),
    )


def downgrade() -> None:
    op.drop_table("efectos_indemnizacion")
    op.drop_table("intentos_indemnizacion")
    op.drop_index("ix_indemnizaciones_estado_proximo", table_name="indemnizaciones")
    op.drop_table("indemnizaciones")
