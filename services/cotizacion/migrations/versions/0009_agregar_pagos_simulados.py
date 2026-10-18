"""Persistir pagos explícitamente simulados.

Revision ID: pagos_simulados
Revises: clima_suscripciones
"""

import sqlalchemy as sa
from alembic import op

revision = "pagos_simulados"
down_revision = "clima_suscripciones"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pagos_simulados",
        sa.Column("pago_id", sa.Uuid(), primary_key=True),
        sa.Column("solicitud_id", sa.String(32), nullable=False, unique=True),
        sa.Column(
            "suscripcion_id", sa.Uuid(),
            sa.ForeignKey("suscripciones.suscripcion_id", ondelete="RESTRICT"),
            nullable=False, unique=True,
        ),
        sa.Column("cliente_id", sa.Uuid(), nullable=False, index=True),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("oferta_id", sa.Uuid(), nullable=False),
        sa.Column("oferta_version", sa.Integer(), nullable=False),
        sa.Column("monto", sa.Numeric(18, 2), nullable=False),
        sa.Column("moneda", sa.String(3), nullable=False),
        sa.Column("periodicidad", sa.String(20), nullable=False),
        sa.Column("confirmada_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("saga_version", sa.Integer(), nullable=False),
        sa.UniqueConstraint("cliente_id", "idempotency_key", name="uq_pagos_simulados_idempotencia"),
    )


def downgrade() -> None:
    op.drop_table("pagos_simulados")
