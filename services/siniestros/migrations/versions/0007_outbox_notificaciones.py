"""Agrega el outbox de notificaciones de siniestros y pagos parametricos."""

import sqlalchemy as sa
from alembic import op

revision = "0007_outbox_notificaciones"
down_revision = "0006_decisiones_siniestro"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "outbox_notificaciones",
        sa.Column("evento_id", sa.String(36), primary_key=True),
        sa.Column("tipo", sa.String(50), nullable=False),
        sa.Column("cliente_id", sa.String(36), nullable=False),
        sa.Column("recurso_tipo", sa.String(20), nullable=False),
        sa.Column("recurso_id", sa.String(36), nullable=False),
        sa.Column("estado", sa.String(30), nullable=True),
        sa.Column("ocurrido_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("publicado_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("intentos", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ultimo_error", sa.String(100), nullable=True),
        sa.CheckConstraint("tipo IN ('siniestro_estado_cambiado', 'pago_parametrico_ejecutado')"),
        sa.CheckConstraint("intentos >= 0"),
    )
    op.create_index(
        "ix_outbox_notificaciones_pendientes",
        "outbox_notificaciones",
        ["publicado_en", "creado_en"],
    )


def downgrade() -> None:
    op.drop_index("ix_outbox_notificaciones_pendientes", table_name="outbox_notificaciones")
    op.drop_table("outbox_notificaciones")
