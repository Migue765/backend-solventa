"""Cancelacion definitiva de polizas con liquidacion y auditoria."""
import sqlalchemy as sa
from alembic import op

revision = "polizas_0004"
down_revision = "polizas_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("polizas") as tabla:
        tabla.add_column(
            sa.Column("estado", sa.String(20), nullable=False, server_default="activa")
        )
    op.create_table(
        "cancelaciones",
        sa.Column("cancelacion_id", sa.Uuid(), primary_key=True),
        sa.Column("poliza_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column("cliente_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("solicitud_hash", sa.String(64), nullable=False),
        sa.Column("motivo", sa.String(40), nullable=False),
        sa.Column("liquidacion", sa.JSON(), nullable=False),
        sa.Column("liquidacion_hash", sa.String(64), nullable=False),
        sa.Column("fecha_efectiva", sa.Date(), nullable=False),
        sa.Column("creada_en", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("cliente_id", "idempotency_key"),
    )


def downgrade() -> None:
    op.drop_table("cancelaciones")
    with op.batch_alter_table("polizas") as tabla:
        tabla.drop_column("estado")
