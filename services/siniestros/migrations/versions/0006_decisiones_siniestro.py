"""Agrega decisiones de aprobacion o enrutamiento y asignaciones de siniestros."""

import sqlalchemy as sa
from alembic import op

revision = "0006_decisiones_siniestro"
down_revision = "0005_pagos_parametricos"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "decisiones_siniestro",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("siniestro_id", sa.String(36), sa.ForeignKey("siniestros.id"), nullable=False),
        sa.Column("operacion", sa.String(36), nullable=False),
        sa.Column("huella", sa.String(64), nullable=False),
        sa.Column("origen", sa.String(12), nullable=False),
        sa.Column("actor_tipo", sa.String(12), nullable=False),
        sa.Column("actor_id", sa.String(36), nullable=True),
        sa.Column("resultado", sa.String(20), nullable=False),
        sa.Column("estado_anterior", sa.String(30), nullable=False),
        sa.Column("estado_nuevo", sa.String(30), nullable=False),
        sa.Column("transiciones", sa.JSON(), nullable=False),
        sa.Column("reglas", sa.JSON(), nullable=False),
        sa.Column("motivo", sa.String(1000), nullable=False),
        sa.Column("decidida_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("correlacion", sa.String(36), nullable=False),
        sa.UniqueConstraint("siniestro_id", "operacion"),
        sa.CheckConstraint("origen IN ('automatica', 'humana')"),
        sa.CheckConstraint("actor_tipo IN ('sistema', 'revisor')"),
        sa.CheckConstraint(
            "resultado IN ('aprobado', 'inspeccion', 'revision', 'bloqueado_fraude', 'rechazado')"
        ),
    )
    op.create_index(
        "ix_decisiones_siniestro_siniestro",
        "decisiones_siniestro",
        ["siniestro_id", "decidida_en"],
    )
    op.create_table(
        "asignaciones_siniestro",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("siniestro_id", sa.String(36), sa.ForeignKey("siniestros.id"), nullable=False),
        sa.Column(
            "decision_id",
            sa.String(36),
            sa.ForeignKey("decisiones_siniestro.id"),
            nullable=False,
        ),
        sa.Column("tipo", sa.String(10), nullable=False),
        sa.Column("destino", sa.String(36), nullable=False),
        sa.Column("estado", sa.String(10), nullable=False),
        sa.Column("asignada_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cerrada_en", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("decision_id"),
        sa.CheckConstraint("tipo IN ('perito', 'cola')"),
        sa.CheckConstraint("estado IN ('activa', 'cerrada')"),
    )
    op.create_index(
        "ix_asignaciones_siniestro_destino",
        "asignaciones_siniestro",
        ["tipo", "destino", "estado", "asignada_en"],
    )
    op.create_index(
        "ix_asignaciones_siniestro_siniestro",
        "asignaciones_siniestro",
        ["siniestro_id", "estado"],
    )


def downgrade() -> None:
    op.drop_index("ix_asignaciones_siniestro_siniestro", table_name="asignaciones_siniestro")
    op.drop_index("ix_asignaciones_siniestro_destino", table_name="asignaciones_siniestro")
    op.drop_table("asignaciones_siniestro")
    op.drop_index("ix_decisiones_siniestro_siniestro", table_name="decisiones_siniestro")
    op.drop_table("decisiones_siniestro")
