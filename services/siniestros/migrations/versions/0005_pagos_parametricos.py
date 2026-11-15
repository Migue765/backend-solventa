"""Agrega pagos parametricos durables, intentos y efectos."""

import sqlalchemy as sa
from alembic import op

revision = "0005_pagos_parametricos"
down_revision = "0004_eventos_parametricos"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table(
        "reglas_parametricas",
        table_args=(
            sa.CheckConstraint("version > 0"),
            sa.CheckConstraint("operador IN ('gt', 'gte', 'lt', 'lte', 'eq')"),
        ),
    ) as batch:
        batch.add_column(sa.Column("beneficiario_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("monto_indemnizacion", sa.Numeric(18, 2), nullable=True))
        batch.add_column(sa.Column("moneda", sa.String(3), nullable=True))
    with op.batch_alter_table(
        "casos_pago_automatico",
        table_args=(sa.CheckConstraint("estado IN ('pendiente', 'entregado')"),),
    ) as batch:
        batch.add_column(sa.Column("rechazado_en", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("ultimo_error", sa.String(100), nullable=True))
    op.create_table(
        "pagos_parametricos",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "caso_id",
            sa.String(36),
            sa.ForeignKey("casos_pago_automatico.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "evento_id",
            sa.String(36),
            sa.ForeignKey("eventos_parametricos.id"),
            nullable=False,
        ),
        sa.Column(
            "siniestro_id",
            sa.String(36),
            sa.ForeignKey("siniestros.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("regla_id", sa.String(36), nullable=False),
        sa.Column("regla_version", sa.Integer(), nullable=False),
        sa.Column("beneficiario_id", sa.String(36), nullable=False),
        sa.Column("monto", sa.Numeric(18, 2), nullable=False),
        sa.Column("moneda", sa.String(3), nullable=False),
        sa.Column("huella", sa.String(64), nullable=False),
        sa.Column("estado", sa.String(20), nullable=False),
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
    )
    op.create_index(
        "ix_pagos_parametricos_estado_proximo",
        "pagos_parametricos",
        ["estado", "proximo_intento_en"],
    )
    op.create_table(
        "intentos_pago_parametrico",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "pago_id",
            sa.String(36),
            sa.ForeignKey("pagos_parametricos.id"),
            nullable=False,
        ),
        sa.Column("numero", sa.Integer(), nullable=False),
        sa.Column("estado", sa.String(20), nullable=False),
        sa.Column("iniciado_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finalizado_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("codigo", sa.String(100), nullable=True),
        sa.CheckConstraint("numero > 0"),
        sa.CheckConstraint("estado IN ('en_proceso', 'ambiguo', 'confirmado', 'rechazado')"),
        sa.UniqueConstraint("pago_id", "numero"),
    )
    op.create_index(
        "ix_intentos_pago_parametrico_pago",
        "intentos_pago_parametrico",
        ["pago_id", "numero"],
    )
    op.create_table(
        "efectos_pago_parametrico",
        sa.Column(
            "pago_id",
            sa.String(36),
            sa.ForeignKey("pagos_parametricos.id"),
            primary_key=True,
        ),
        sa.Column("huella", sa.String(64), nullable=False),
        sa.Column("resultado", sa.String(20), nullable=False),
        sa.Column("evaluado_en", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("resultado IN ('confirmado', 'rechazado')"),
    )


def downgrade() -> None:
    op.drop_table("efectos_pago_parametrico")
    op.drop_index(
        "ix_intentos_pago_parametrico_pago",
        table_name="intentos_pago_parametrico",
    )
    op.drop_table("intentos_pago_parametrico")
    op.drop_index(
        "ix_pagos_parametricos_estado_proximo",
        table_name="pagos_parametricos",
    )
    op.drop_table("pagos_parametricos")
    with op.batch_alter_table(
        "casos_pago_automatico",
        table_args=(sa.CheckConstraint("estado IN ('pendiente', 'entregado')"),),
    ) as batch:
        batch.drop_column("ultimo_error")
        batch.drop_column("rechazado_en")
    with op.batch_alter_table(
        "reglas_parametricas",
        table_args=(
            sa.CheckConstraint("version > 0"),
            sa.CheckConstraint("operador IN ('gt', 'gte', 'lt', 'lte', 'eq')"),
        ),
    ) as batch:
        batch.drop_column("moneda")
        batch.drop_column("monto_indemnizacion")
        batch.drop_column("beneficiario_id")
