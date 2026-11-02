"""Registrar resultados del simulador sin alterar pagos historicos."""

import sqlalchemy as sa
from alembic import op

revision = "intentos_pago_simulado"
down_revision = "ofertas_socios_hipotecarias"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "intentos_pago_simulado",
        sa.Column("intento_id", sa.Uuid(), primary_key=True),
        sa.Column("solicitud_id", sa.String(32), nullable=False, index=True),
        sa.Column(
            "suscripcion_id",
            sa.Uuid(),
            sa.ForeignKey("suscripciones.suscripcion_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("cliente_id", sa.Uuid(), nullable=False, index=True),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("oferta_id", sa.Uuid(), nullable=False),
        sa.Column("oferta_version", sa.Integer(), nullable=False),
        sa.Column("monto", sa.Numeric(18, 2), nullable=False),
        sa.Column("moneda", sa.String(3), nullable=False),
        sa.Column("periodicidad", sa.String(20), nullable=False),
        sa.Column("resultado", sa.String(20), nullable=False),
        sa.Column("evaluado_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("saga_version", sa.Integer(), nullable=False),
        sa.Column("saga_actualizada_en", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("cliente_id", "idempotency_key", name="uq_intentos_pago_idempotencia"),
        sa.CheckConstraint(
            "resultado IN ('confirmado', 'rechazado')", name="ck_intentos_pago_resultado"
        ),
    )


def downgrade() -> None:
    op.drop_table("intentos_pago_simulado")
