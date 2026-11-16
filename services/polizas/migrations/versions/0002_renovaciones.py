"""Renovacion academica de polizas con pago simulado idempotente."""
import sqlalchemy as sa
from alembic import op

revision = "polizas_0002"
down_revision = "polizas_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "renovaciones",
        sa.Column("renovacion_id", sa.Uuid(), primary_key=True),
        sa.Column("poliza_id", sa.Uuid(), nullable=False, index=True),
        sa.Column("cliente_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("solicitud_hash", sa.String(64), nullable=False),
        sa.Column("reserva_periodo", sa.String(80), nullable=True, unique=True),
        sa.Column("fin_anterior", sa.Date(), nullable=False),
        sa.Column("nuevo_inicio", sa.Date(), nullable=False),
        sa.Column("nuevo_fin", sa.Date(), nullable=False),
        sa.Column("condiciones", sa.JSON(), nullable=False),
        sa.Column("condiciones_hash", sa.String(64), nullable=False),
        sa.Column("regla_version", sa.String(40), nullable=False),
        sa.Column("estado", sa.String(20), nullable=False),
        sa.Column("motivo", sa.String(40), nullable=True),
        sa.Column("intentos_pago", sa.Integer(), nullable=False),
        sa.Column("creada_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actualizada_en", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("cliente_id", "idempotency_key"),
    )
    op.create_table(
        "pagos_renovacion",
        sa.Column("renovacion_id", sa.Uuid(), primary_key=True),
        sa.Column("monto", sa.String(20), nullable=False),
        sa.Column("moneda", sa.String(3), nullable=False),
        sa.Column("resultado", sa.String(20), nullable=False),
        sa.Column("evaluado_en", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("pagos_renovacion")
    op.drop_table("renovaciones")
