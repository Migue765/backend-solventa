"""Estado vigente e historial de estados de siniestros."""
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
        sa.Column("tipo", sa.String(20), nullable=False),
        sa.Column("estado", sa.String(30), nullable=False),
        sa.Column("ocurrido_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reportado_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actualizado_en", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "historial_estados_siniestro",
        sa.Column(
            "siniestro_id",
            sa.Uuid(),
            sa.ForeignKey("siniestros.siniestro_id"),
            primary_key=True,
        ),
        sa.Column("secuencia", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("estado", sa.String(30), nullable=False),
        sa.Column("ocurrido_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("visible_cliente", sa.Boolean(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("historial_estados_siniestro")
    op.drop_table("siniestros")
