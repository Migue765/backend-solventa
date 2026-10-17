"""agregar pagos y polizas

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-16
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ordenes_pago",
        sa.Column("orden_pago_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "suscripcion_id",
            sa.Uuid(),
            sa.ForeignKey("suscripciones.suscripcion_id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("solicitud_id", sa.String(32), nullable=False, unique=True),
        sa.Column("cliente_id", sa.Uuid(), nullable=False),
        sa.Column("monto", sa.Numeric(18, 2), nullable=False),
        sa.Column("moneda", sa.String(3), nullable=False),
        sa.Column("metodo_pago", sa.String(20), nullable=False),
        sa.Column("estado", sa.String(20), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("creada_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmada_en", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "cliente_id",
            "idempotency_key",
            name="uq_ordenes_pago_cliente_idempotencia",
        ),
    )
    op.create_index("ix_ordenes_pago_cliente_id", "ordenes_pago", ["cliente_id"])
    op.create_table(
        "transacciones_pago",
        sa.Column("transaccion_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "orden_pago_id",
            sa.Uuid(),
            sa.ForeignKey("ordenes_pago.orden_pago_id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("estado", sa.String(20), nullable=False),
        sa.Column("referencia", sa.String(40), nullable=False, unique=True),
        sa.Column("procesada_en", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "polizas",
        sa.Column("poliza_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "suscripcion_id",
            sa.Uuid(),
            sa.ForeignKey("suscripciones.suscripcion_id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("cliente_id", sa.Uuid(), nullable=False),
        sa.Column("producto_id", sa.Uuid(), nullable=False),
        sa.Column("numero", sa.String(40), nullable=False, unique=True),
        sa.Column("estado", sa.String(20), nullable=False),
        sa.Column("prima_mensual", sa.Numeric(18, 2), nullable=False),
        sa.Column("moneda", sa.String(3), nullable=False),
        sa.Column("vigente_desde", sa.DateTime(timezone=True), nullable=False),
        sa.Column("vigente_hasta", sa.DateTime(timezone=True), nullable=False),
        sa.Column("emitida_en", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_polizas_cliente_id", "polizas", ["cliente_id"])


def downgrade() -> None:
    op.drop_index("ix_polizas_cliente_id", table_name="polizas")
    op.drop_table("polizas")
    op.drop_table("transacciones_pago")
    op.drop_index("ix_ordenes_pago_cliente_id", table_name="ordenes_pago")
    op.drop_table("ordenes_pago")
