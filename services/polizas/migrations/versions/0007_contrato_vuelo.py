"""Contrato de vuelo comprado, sin reconstruir contratos historicos."""

import sqlalchemy as sa
from alembic import op

revision = "polizas_0007"
down_revision = "polizas_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("polizas", sa.Column("contrato_vuelo", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("polizas", "contrato_vuelo")
