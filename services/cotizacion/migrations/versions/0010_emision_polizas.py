"""Marcar exclusivamente pagos nuevos elegibles para emision academica."""
import sqlalchemy as sa
from alembic import op

revision = "emision_polizas"
down_revision = "pagos_simulados"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pagos_simulados",
        sa.Column("emision_solicitada", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("pagos_simulados", "emision_solicitada")
