"""Capacidades internas explicitas; no convierte cuentas ni concede permisos."""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "usuarios_permisos",
        sa.Column(
            "usuario_id",
            sa.Uuid(),
            sa.ForeignKey("usuarios.usuario_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("permiso", sa.String(100), primary_key=True),
    )


def downgrade() -> None:
    op.drop_table("usuarios_permisos")
