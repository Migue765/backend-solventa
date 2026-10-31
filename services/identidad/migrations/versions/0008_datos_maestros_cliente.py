"""Agrega telefono y fecha de nacimiento maestros al cliente."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("usuarios") as batch:
        batch.add_column(sa.Column("telefono", sa.String(length=13), nullable=True))
        batch.add_column(sa.Column("fecha_nacimiento", sa.Date(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("usuarios") as batch:
        batch.drop_column("fecha_nacimiento")
        batch.drop_column("telefono")
