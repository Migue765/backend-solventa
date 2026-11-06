"""Vincula resultados de sincronizacion sin alterar la auditoria existente."""

import sqlalchemy as sa
from alembic import op

revision = "0003_resultados_operacion"
down_revision = "0002_estado_historial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("auditoria_siniestros", sa.Column("operation", sa.String(36), nullable=True))
    op.add_column("auditoria_siniestros", sa.Column("fingerprint", sa.String(64), nullable=True))
    op.add_column("auditoria_siniestros", sa.Column("http_status", sa.Integer(), nullable=True))
    op.create_index(
        "ix_auditoria_operacion",
        "auditoria_siniestros",
        ["actor", "action", "operation", "created"],
    )


def downgrade() -> None:
    op.drop_index("ix_auditoria_operacion", table_name="auditoria_siniestros")
    op.drop_column("auditoria_siniestros", "http_status")
    op.drop_column("auditoria_siniestros", "fingerprint")
    op.drop_column("auditoria_siniestros", "operation")
