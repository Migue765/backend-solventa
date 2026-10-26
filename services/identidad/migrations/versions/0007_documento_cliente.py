"""Agrega documento maestro opcional al cliente."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("usuarios") as batch:
        batch.add_column(sa.Column("tipo_documento", sa.String(length=16), nullable=True))
        batch.add_column(sa.Column("numero_documento", sa.String(length=16), nullable=True))
        batch.create_check_constraint(
            "ck_usuarios_documento_completo",
            "(tipo_documento IS NULL AND numero_documento IS NULL) OR "
            "(tipo_documento IS NOT NULL AND numero_documento IS NOT NULL)",
        )
        batch.create_unique_constraint(
            "uq_usuarios_documento", ["tipo_documento", "numero_documento"]
        )


def downgrade() -> None:
    with op.batch_alter_table("usuarios") as batch:
        batch.drop_constraint("uq_usuarios_documento", type_="unique")
        batch.drop_constraint("ck_usuarios_documento_completo", type_="check")
        batch.drop_column("numero_documento")
        batch.drop_column("tipo_documento")
