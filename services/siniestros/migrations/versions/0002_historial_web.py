"""Conserva estado e historial inicial de los siniestros compartidos."""

import sqlalchemy as sa
from alembic import op

revision = "0002_historial_web"
down_revision = "0001_siniestros"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "siniestros",
        sa.Column("state", sa.String(30), nullable=False, server_default="registrado"),
    )
    op.add_column(
        "siniestros",
        sa.Column("history", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )
    claims = sa.table(
        "siniestros",
        sa.column("id", sa.String(36)),
        sa.column("owner", sa.String(36)),
        sa.column("created", sa.String(40)),
        sa.column("history", sa.JSON()),
    )
    connection = op.get_bind()
    for row in connection.execute(sa.select(claims.c.id, claims.c.owner, claims.c.created)):
        connection.execute(
            claims.update().where(claims.c.id == row.id).values(
                history=[{"estado": "registrado", "fecha": row.created, "actorId": row.owner}]
            )
        )


def downgrade() -> None:
    op.drop_column("siniestros", "history")
    op.drop_column("siniestros", "state")
