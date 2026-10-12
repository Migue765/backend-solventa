"""reglas de tarifa versionadas, instantaneas de decision y outbox

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-11
"""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

PRODUCTO_AUTOMOTRIZ = UUID("11111111-1111-4111-8111-111111111111")
REGLA_INICIAL = UUID("aaaaaaaa-0000-4000-8000-000000000001")


def upgrade() -> None:
    reglas = op.create_table(
        "reglas_tarifa",
        sa.Column("regla_id", sa.Uuid(), primary_key=True),
        sa.Column("version", sa.String(40), nullable=False),
        sa.Column("producto_id", sa.Uuid(), nullable=False),
        sa.Column("tasa", sa.Numeric(10, 6), nullable=False),
        sa.Column("factor_perfil", sa.Numeric(10, 6), nullable=False),
        sa.Column("vigente_desde", sa.DateTime(timezone=True), nullable=False),
        sa.Column("vigente_hasta", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("producto_id", "version", name="uq_reglas_producto_version"),
    )
    op.create_index("ix_reglas_tarifa_producto_id", "reglas_tarifa", ["producto_id"])

    op.create_table(
        "instantaneas_decision",
        sa.Column(
            "cotizacion_id",
            sa.Uuid(),
            sa.ForeignKey("cotizaciones.cotizacion_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("regla_id", sa.Uuid(), nullable=False),
        sa.Column("regla_version", sa.String(40), nullable=False),
        sa.Column("perfil_id", sa.String(64), nullable=False),
        sa.Column("perfil_modelo_version", sa.String(40), nullable=False),
        sa.Column("hash_decision", sa.String(64), nullable=False),
        sa.Column("hash_contenido", sa.String(64), nullable=False),
        sa.Column("contenido", sa.JSON(), nullable=False),
        sa.Column("creada_en", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_instantaneas_decision_hash_decision", "instantaneas_decision", ["hash_decision"]
    )

    op.create_table(
        "outbox_eventos",
        sa.Column("evento_id", sa.Uuid(), primary_key=True),
        sa.Column("tipo", sa.String(80), nullable=False),
        sa.Column("agregado_id", sa.Uuid(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("publicado_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("intentos", sa.Integer(), nullable=False),
    )
    op.create_index("ix_outbox_eventos_agregado_id", "outbox_eventos", ["agregado_id"])
    op.create_index("ix_outbox_eventos_publicado_en", "outbox_eventos", ["publicado_en"])

    op.bulk_insert(
        reglas,
        [
            {
                "regla_id": REGLA_INICIAL,
                "version": "tarifa-2026.1",
                "producto_id": PRODUCTO_AUTOMOTRIZ,
                "tasa": Decimal("0.015"),
                "factor_perfil": Decimal("0.10"),
                "vigente_desde": datetime(2026, 1, 1, tzinfo=timezone.utc),
                "vigente_hasta": None,
            }
        ],
    )


def downgrade() -> None:
    op.drop_table("outbox_eventos")
    op.drop_table("instantaneas_decision")
    op.drop_table("reglas_tarifa")
