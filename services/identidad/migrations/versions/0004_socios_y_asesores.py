"""Socios distribuidores y asesores autorizados."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "socios",
        sa.Column("socio_id", sa.String(50), primary_key=True),
        sa.Column("nombre", sa.String(120), nullable=False),
        sa.Column("activo", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_table(
        "asesores_socios",
        sa.Column(
            "asesor_id",
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
    op.drop_table("asesores_socios")
    op.drop_table("socios")
