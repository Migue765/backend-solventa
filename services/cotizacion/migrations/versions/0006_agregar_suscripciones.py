"""agregar suscripciones y saga de emision

Revision ID: 0006
Revises: academico_v1
Create Date: 2026-10-16
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "academico_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "suscripciones",
        sa.Column("suscripcion_id", sa.Uuid(), primary_key=True),
        sa.Column("solicitud_id", sa.String(32), nullable=False, unique=True),
        sa.Column(
            "cotizacion_id",
            sa.Uuid(),
            sa.ForeignKey("cotizaciones.cotizacion_id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("cliente_id", sa.Uuid(), nullable=False),
        sa.Column("oferta_id", sa.Uuid(), nullable=False),
        sa.Column("oferta_version", sa.Integer(), nullable=False),
        sa.Column("estado", sa.String(30), nullable=False),
        sa.Column("regla_version", sa.String(40), nullable=False),
        sa.Column("creada_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actualizada_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.UniqueConstraint(
            "cliente_id",
            "idempotency_key",
            name="uq_suscripciones_cliente_idempotencia",
        ),
    )
    op.create_index("ix_suscripciones_cliente_id", "suscripciones", ["cliente_id"])

    op.create_table(
        "decisiones_suscripcion",
        sa.Column(
            "suscripcion_id",
            sa.Uuid(),
            sa.ForeignKey("suscripciones.suscripcion_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("regla_version", sa.String(40), nullable=False),
        sa.Column("resultado", sa.String(30), nullable=False),
        sa.Column("datos_evaluados", sa.JSON(), nullable=False),
        sa.Column("hash_decision", sa.String(64), nullable=False),
        sa.Column("decidida_en", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_decisiones_suscripcion_hash_decision",
        "decisiones_suscripcion",
        ["hash_decision"],
    )

    op.create_table(
        "sagas_emision",
        sa.Column(
            "suscripcion_id",
            sa.Uuid(),
            sa.ForeignKey("suscripciones.suscripcion_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("estado", sa.String(30), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("actualizada_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ultimo_error", sa.String(500), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("sagas_emision")
    op.drop_index(
        "ix_decisiones_suscripcion_hash_decision",
        table_name="decisiones_suscripcion",
    )
    op.drop_table("decisiones_suscripcion")
    op.drop_index("ix_suscripciones_cliente_id", table_name="suscripciones")
    op.drop_table("suscripciones")
