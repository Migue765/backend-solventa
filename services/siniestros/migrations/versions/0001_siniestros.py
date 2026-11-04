"""Siniestros, operaciones de carga y auditoria durables."""

import sqlalchemy as sa
from alembic import op

revision = "0001_siniestros"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "siniestros",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner", sa.String(36), nullable=False),
        sa.Column("operation", sa.String(36), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created", sa.String(40), nullable=False),
        sa.UniqueConstraint("owner", "operation"),
    )
    op.create_index("ix_siniestros_owner", "siniestros", ["owner"])
    op.create_table(
        "cargas_siniestros",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("claim_id", sa.String(36), sa.ForeignKey("siniestros.id"), nullable=False),
        sa.Column("owner", sa.String(36), nullable=False),
        sa.Column("operation", sa.String(36), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("mime", sa.String(40), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("location", sa.JSON(), nullable=True),
        sa.Column("captured", sa.String(40), nullable=True),
        sa.Column("offset", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created", sa.String(40), nullable=False),
        sa.Column("confirmed", sa.String(40), nullable=True),
        sa.UniqueConstraint("owner", "operation"),
        sa.CheckConstraint("offset >= 0 AND offset <= size"),
        sa.CheckConstraint("status IN ('pendiente', 'confirmada', 'cancelada')"),
    )
    op.create_index("ix_cargas_siniestros_claim_id", "cargas_siniestros", ["claim_id"])
    op.create_table(
        "auditoria_siniestros",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("actor", sa.String(36), nullable=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("result", sa.String(64), nullable=False),
        sa.Column("correlation", sa.String(36), nullable=False),
        sa.Column("created", sa.String(40), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("auditoria_siniestros")
    op.drop_table("cargas_siniestros")
    op.drop_table("siniestros")
