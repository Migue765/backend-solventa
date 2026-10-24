"""Cuotas y reservas de operaciones por socio compartidas entre replicas."""

import sqlalchemy as sa
from alembic import op

revision = "admision_socios"
down_revision = "operaciones_hipotecarias"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "operaciones_socios",
        sa.Column("socio_id", sa.String(50), primary_key=True),
        sa.Column("operation_id", sa.Uuid(), primary_key=True),
        sa.Column("usuario_id", sa.Uuid(), nullable=False),
        sa.Column("cliente_id", sa.Uuid(), nullable=False, index=True),
        sa.Column("producto_id", sa.String(64), nullable=False),
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
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("creada_en", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("operation_id", name="uq_operaciones_socios_operation"),
    )
    op.create_table(
        "admision_socios",
        sa.Column("socio_id", sa.String(50), primary_key=True),
        sa.Column("ventana", sa.DateTime(timezone=True), nullable=False),
        sa.Column("usadas", sa.Integer(), nullable=False),
        sa.Column("cuota_por_minuto", sa.Integer(), nullable=True),
        sa.Column("max_concurrentes", sa.Integer(), nullable=True),
    )
    op.create_table(
        "reservas_operaciones",
        sa.Column("socio_id", sa.String(50), primary_key=True),
        sa.Column("operation_id", sa.Uuid(), primary_key=True),
        sa.Column("operation_global", sa.Uuid(), nullable=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("token", sa.Uuid(), nullable=True),
        sa.Column("vence_en", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("operation_global", name="uq_reservas_operation_global"),
    )
    op.create_index("ix_reservas_operaciones_vence_en", "reservas_operaciones", ["vence_en"])


def downgrade() -> None:
    op.drop_table("reservas_operaciones")
    op.drop_table("admision_socios")
    op.drop_table("operaciones_socios")
