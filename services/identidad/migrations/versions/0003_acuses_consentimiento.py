"""Acuses y reintentos del Outbox de consentimiento."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "outbox_consentimientos",
        sa.Column("intentos", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("outbox_consentimientos", sa.Column("ultimo_error", sa.String(80)))
    op.add_column("outbox_consentimientos", sa.Column("acuse", sa.JSON()))


def downgrade() -> None:
    op.drop_column("outbox_consentimientos", "acuse")
    op.drop_column("outbox_consentimientos", "ultimo_error")
    op.drop_column("outbox_consentimientos", "intentos")
