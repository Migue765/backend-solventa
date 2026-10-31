"""Usuarios de plataforma socio, independientes de los asesores."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "usuarios_socios",
        sa.Column(
            "usuario_id",
            sa.Uuid(),
            sa.ForeignKey("usuarios.usuario_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "socio_id",
            sa.String(50),
            sa.ForeignKey("socios.socio_id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )


def downgrade() -> None:
    op.drop_table("usuarios_socios")
