"""Rechazo explicito del cliente, separado de pagos y suscripciones."""

import sqlalchemy as sa
from alembic import op

revision = "rechazos_solicitud"
down_revision = "pagos_simulados_pendientes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rechazos_solicitud",
        sa.Column(
            "cotizacion_id",
            sa.Uuid(),
            sa.ForeignKey("cotizaciones.cotizacion_id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("solicitud_id", sa.String(32), nullable=False, unique=True),
        sa.Column("cliente_id", sa.Uuid(), nullable=False),
        sa.Column("rechazada_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.UniqueConstraint("cliente_id", "idempotency_key", name="uq_rechazo_cliente_clave"),
    )
    op.create_index("ix_rechazos_solicitud_cliente_id", "rechazos_solicitud", ["cliente_id"])


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(sa.text("LOCK TABLE rechazos_solicitud IN ACCESS EXCLUSIVE MODE"))
    else:
        connection.execute(
            sa.text("UPDATE rechazos_solicitud SET cliente_id = cliente_id WHERE 1=0")
        )
    if connection.scalar(sa.text("SELECT COUNT(*) FROM rechazos_solicitud")):
        raise RuntimeError("Conserve los rechazos y claves; revierta codigo sin borrar decisiones.")
    op.drop_table("rechazos_solicitud")
