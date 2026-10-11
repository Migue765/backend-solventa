"""crear tablas de cotizacion

Revision ID: 0001
Revises:
Create Date: 2026-10-08
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cotizaciones",
        sa.Column("cotizacion_id", sa.Uuid(), primary_key=True),
        sa.Column("cliente_id", sa.Uuid(), nullable=False),
        sa.Column("producto_id", sa.Uuid(), nullable=False),
        sa.Column("estado", sa.String(20), nullable=False),
        sa.Column("moneda", sa.String(3), nullable=False),
        sa.Column("prima_estimada", sa.Numeric(18, 2), nullable=False),
        sa.Column("valida_hasta", sa.DateTime(timezone=True), nullable=False),
        sa.Column("creada_en", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_cotizaciones_cliente_id", "cotizaciones", ["cliente_id"])

    op.create_table(
        "coberturas_cotizadas",
        sa.Column(
            "cotizacion_id",
            sa.Uuid(),
            sa.ForeignKey("cotizaciones.cotizacion_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("cobertura_id", sa.Uuid(), primary_key=True),
        sa.Column("suma_asegurada", sa.Numeric(18, 2), nullable=False),
        sa.Column("deducible", sa.Numeric(18, 2), nullable=False),
        sa.Column("prima", sa.Numeric(18, 2), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("coberturas_cotizadas")
    op.drop_index("ix_cotizaciones_cliente_id", table_name="cotizaciones")
    op.drop_table("cotizaciones")
