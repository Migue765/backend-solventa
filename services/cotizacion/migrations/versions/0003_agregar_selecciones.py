"""agregar selecciones de oferta

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-11
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "selecciones_oferta",
        sa.Column(
            "cotizacion_id",
            sa.Uuid(),
            sa.ForeignKey("cotizaciones.cotizacion_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("solicitud_id", sa.String(32), nullable=False, unique=True),
        sa.Column("oferta_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("nombre_oferta", sa.String(120), nullable=False),
        sa.Column("prima_mensual", sa.Numeric(10, 4), nullable=False),
        sa.Column("moneda", sa.String(3), nullable=False),
        sa.Column("seleccionada_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("selecciones_oferta")
