"""agregar solicitudes de cotizacion

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-11
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("coberturas_cotizadas") as batch:
        batch.add_column(
            sa.Column("orden", sa.Integer(), nullable=False, server_default="0")
        )

    with op.batch_alter_table("cotizaciones") as batch:
        batch.add_column(sa.Column("solicitud_id", sa.String(32), nullable=True))
        batch.add_column(sa.Column("tipo_cliente", sa.String(30), nullable=True))
        batch.add_column(sa.Column("identificacion", sa.String(20), nullable=True))
        batch.add_column(sa.Column("patente_vehiculo", sa.String(8), nullable=True))
        batch.add_column(sa.Column("correo", sa.String(254), nullable=True))
        batch.add_column(sa.Column("telefono", sa.String(16), nullable=True))
        batch.add_column(sa.Column("idempotency_key", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("request_fingerprint", sa.String(64), nullable=True))
        batch.create_unique_constraint("uq_cotizaciones_solicitud_id", ["solicitud_id"])
        batch.create_unique_constraint(
            "uq_cotizaciones_cliente_idempotencia",
            ["cliente_id", "idempotency_key"],
        )


def downgrade() -> None:
    with op.batch_alter_table("cotizaciones") as batch:
        batch.drop_constraint("uq_cotizaciones_cliente_idempotencia", type_="unique")
        batch.drop_constraint("uq_cotizaciones_solicitud_id", type_="unique")
        batch.drop_column("request_fingerprint")
        batch.drop_column("idempotency_key")
        batch.drop_column("telefono")
        batch.drop_column("correo")
        batch.drop_column("patente_vehiculo")
        batch.drop_column("identificacion")
        batch.drop_column("tipo_cliente")
        batch.drop_column("solicitud_id")

    with op.batch_alter_table("coberturas_cotizadas") as batch:
        batch.drop_column("orden")
