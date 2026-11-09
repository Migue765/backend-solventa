"""Agrega estado e historial sin reemplazar avisos ni cargas existentes."""

import sqlalchemy as sa
from alembic import op

revision = "0002_estado_historial"
down_revision = "0001_siniestros"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("siniestros", sa.Column("tipo", sa.String(20), nullable=True))
    op.add_column(
        "siniestros",
        sa.Column("estado", sa.String(30), nullable=False, server_default="reportado"),
    )
    op.add_column(
        "siniestros", sa.Column("actualizado_en", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_table(
        "historial_estados_siniestro",
        sa.Column(
            "siniestro_id", sa.String(36), sa.ForeignKey("siniestros.id"), primary_key=True
        ),
        sa.Column("secuencia", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("estado", sa.String(30), nullable=False),
        sa.Column("ocurrido_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("visible_cliente", sa.Boolean(), nullable=False),
    )
    timestamp = (
        "CAST(created AS TIMESTAMP WITH TIME ZONE)"
        if op.get_context().dialect.name == "postgresql"
        else "created"
    )
    op.execute(f"UPDATE siniestros SET actualizado_en = {timestamp}")
    op.execute(
        "INSERT INTO historial_estados_siniestro "
        "(siniestro_id, secuencia, estado, ocurrido_en, visible_cliente) "
        f"SELECT id, 1, 'reportado', {timestamp}, true FROM siniestros"
    )


def downgrade() -> None:
    op.drop_table("historial_estados_siniestro")
    op.drop_column("siniestros", "actualizado_en")
    op.drop_column("siniestros", "estado")
    op.drop_column("siniestros", "tipo")
