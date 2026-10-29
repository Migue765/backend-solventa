"""Ofertas persistidas de las operaciones de socios (SOL-330)."""

import sqlalchemy as sa
from alembic import op

revision = "ofertas_socios"
down_revision = "admision_socios"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ofertas_socios",
        sa.Column("oferta_socio_id", sa.Uuid(), primary_key=True),
        sa.Column("socio_id", sa.String(50), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("cliente_id", sa.Uuid(), nullable=False, index=True),
        sa.Column("producto_id", sa.String(64), nullable=False),
        sa.Column(
            "solicitud_id",
            sa.String(32),
            sa.ForeignKey("cotizaciones.solicitud_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "cotizacion_id",
            sa.Uuid(),
            sa.ForeignKey("cotizaciones.cotizacion_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("hash_decision", sa.String(64), nullable=False),
        sa.Column("hash_contenido", sa.String(64), nullable=False),
        sa.Column("contenido", sa.JSON(), nullable=False),
        sa.Column("calculada_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("vigente_hasta", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("socio_id", "operation_id", name="uq_ofertas_socios_operacion"),
        sa.ForeignKeyConstraint(
            ["socio_id", "operation_id"],
            ["operaciones_socios.socio_id", "operaciones_socios.operation_id"],
            ondelete="RESTRICT",
        ),
    )


def downgrade() -> None:
    op.drop_table("ofertas_socios")
