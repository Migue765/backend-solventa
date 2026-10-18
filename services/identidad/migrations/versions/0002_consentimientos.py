"""Consentimiento de perfilamiento y outbox."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "consentimientos_perfilamiento",
        sa.Column("cliente_id", sa.Uuid(), sa.ForeignKey("usuarios.usuario_id"), primary_key=True),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("contenido", sa.JSON(), nullable=False),
    )
    op.create_table(
        "outbox_consentimientos",
        sa.Column("evento_id", sa.Uuid(), primary_key=True),
        sa.Column("tipo", sa.String(60), nullable=False),
        sa.Column("contenido", sa.JSON(), nullable=False),
        sa.Column("publicado", sa.Boolean(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("outbox_consentimientos")
    op.drop_table("consentimientos_perfilamiento")
