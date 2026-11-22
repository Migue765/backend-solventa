"""Agrega tokens de recuperacion de contrasena de un solo uso."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "recuperaciones_contrasena",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("usuario_id", sa.Uuid(), sa.ForeignKey("usuarios.usuario_id"), nullable=False),
        sa.Column("creada_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expira_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumida_en", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_recuperaciones_contrasena_usuario_id", "recuperaciones_contrasena", ["usuario_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_recuperaciones_contrasena_usuario_id", table_name="recuperaciones_contrasena")
    op.drop_table("recuperaciones_contrasena")
