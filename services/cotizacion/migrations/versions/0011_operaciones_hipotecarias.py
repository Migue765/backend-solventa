"""Operaciones de perfilamiento hipotecario."""

import sqlalchemy as sa
from alembic import op

revision = "operaciones_hipotecarias"
down_revision = "emision_polizas"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "operaciones_hipotecarias",
        sa.Column("operation_id", sa.Uuid(), primary_key=True),
        sa.Column("socio_id", sa.String(50), primary_key=True),
        sa.Column("asesor_id", sa.Uuid(), nullable=False, index=True),
        sa.Column("cliente_id", sa.Uuid(), nullable=False, index=True),
        sa.Column("consentimiento_id", sa.Uuid(), nullable=False),
        sa.Column("consentimiento_version", sa.Integer(), nullable=False),
        sa.Column("referencia_credito", sa.String(30), nullable=False),
        sa.Column("monto_credito", sa.Numeric(18, 2), nullable=False),
        sa.Column("plazo_meses", sa.Integer(), nullable=False),
        sa.Column(
            "solicitud_id",
            sa.String(32),
            sa.ForeignKey("cotizaciones.solicitud_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "cotizacion_id",
            sa.Uuid(),
            sa.ForeignKey("cotizaciones.cotizacion_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("perfilamiento_referencia", sa.String(64), nullable=False),
        sa.Column("estado", sa.String(20), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("creada_en", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("socio_id", "operation_id", name="uq_operacion_hipotecaria_socio"),
    )


def downgrade() -> None:
    op.drop_table("operaciones_hipotecarias")
