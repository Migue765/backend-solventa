"""Ofertas hipotecarias explicables e inmutables."""

import sqlalchemy as sa
from alembic import op

revision = "ofertas_hipotecarias"
down_revision = "admision_socios"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ofertas_hipotecarias",
        sa.Column("socio_id", sa.String(50), primary_key=True),
        sa.Column("operation_id", sa.Uuid(), primary_key=True),
        sa.Column("oferta_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("contenido", sa.JSON(), nullable=False),
        sa.Column("evidencia_decision", sa.JSON(), nullable=False),
        sa.Column("hash_oferta", sa.String(64), nullable=False),
        sa.Column("hash_decision", sa.String(64), nullable=False),
        sa.Column("creada_en", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["socio_id", "operation_id"],
            ["operaciones_socios.socio_id", "operaciones_socios.operation_id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_ofertas_hipotecarias_hash_oferta", "ofertas_hipotecarias", ["hash_oferta"])
    op.create_index(
        "ix_ofertas_hipotecarias_hash_decision",
        "ofertas_hipotecarias",
        ["hash_decision"],
    )


def downgrade() -> None:
    op.drop_table("ofertas_hipotecarias")
