"""Persistencia propia de polizas academicas."""
import sqlalchemy as sa
from alembic import op

revision = "polizas_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "polizas",
        sa.Column("poliza_id", sa.Uuid(), primary_key=True),
        sa.Column("cliente_id", sa.Uuid(), nullable=False, index=True),
        sa.Column("suscripcion_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column("pago_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column("tipo", sa.String(30), nullable=False),
        sa.Column("fecha_inicio", sa.Date(), nullable=False),
        sa.Column("fecha_fin", sa.Date(), nullable=False),
        sa.Column("emitida_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidencia_hash", sa.String(64), nullable=False),
        sa.Column("regla_version", sa.String(40), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("polizas")
