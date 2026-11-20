"""Agrega sesiones e intentos de demostracion de identidad sin imagenes ni PII."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "verificaciones_identidad_simuladas",
        sa.Column("verificacion_id", sa.Uuid(), primary_key=True),
        sa.Column("usuario_id", sa.Uuid(), sa.ForeignKey("usuarios.usuario_id"), nullable=False),
        sa.Column("estado", sa.String(12), nullable=False),
        sa.Column("intentos", sa.Integer(), nullable=False),
        sa.Column("motivo", sa.String(32), nullable=True),
        sa.Column("resultados", sa.JSON(), nullable=False),
        sa.UniqueConstraint("usuario_id"),
    )
    op.create_table(
        "intentos_identidad_simulados",
        sa.Column(
            "verificacion_id",
            sa.Uuid(),
            sa.ForeignKey("verificaciones_identidad_simuladas.verificacion_id"),
            primary_key=True,
        ),
        sa.Column("intento_id", sa.Uuid(), primary_key=True),
        sa.Column("solicitud", sa.JSON(), nullable=False),
        sa.Column("respuesta", sa.JSON(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("intentos_identidad_simulados")
    op.drop_table("verificaciones_identidad_simuladas")
