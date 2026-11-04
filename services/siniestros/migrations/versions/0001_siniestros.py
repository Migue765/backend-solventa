"""Registro durable e idempotente de siniestros."""
import sqlalchemy as sa
from alembic import op

revision = "siniestros_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "siniestros",
        sa.Column("siniestro_id", sa.Uuid(), primary_key=True),
        sa.Column("cliente_id", sa.Uuid(), nullable=False, index=True),
        sa.Column("poliza_id", sa.Uuid(), nullable=False, index=True),
        sa.Column("clave_idempotencia", sa.Uuid(), nullable=False),
        sa.Column("contenido_hash", sa.String(64), nullable=False),
        sa.Column("fecha_ocurrencia", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tipo_evento", sa.String(100), nullable=False),
        sa.Column("descripcion", sa.String(5000), nullable=False),
        sa.Column("lugar", sa.String(300), nullable=False),
        sa.Column("reportado_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("estado", sa.String(30), nullable=False),
        sa.Column("historial", sa.JSON(), nullable=False),
        sa.UniqueConstraint("cliente_id", "clave_idempotencia"),
    )


def downgrade() -> None:
    op.drop_table("siniestros")
