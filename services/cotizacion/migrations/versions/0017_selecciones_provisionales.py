"""Seleccion provisional separada de las aceptaciones historicas."""

import sqlalchemy as sa
from alembic import op

revision = "selecciones_provisionales"
down_revision = "conciliacion_emision"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "selecciones_provisionales",
        sa.Column(
            "cotizacion_id",
            sa.Uuid(),
            sa.ForeignKey("cotizaciones.cotizacion_id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("revision", sa.Integer(), primary_key=True),
        sa.Column("cliente_id", sa.Uuid(), nullable=False),
        sa.Column("solicitud_id", sa.String(32), nullable=False),
        sa.Column("activa", sa.Boolean(), nullable=False),
        sa.Column("oferta_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("nombre_oferta", sa.String(120), nullable=False),
        sa.Column("prima_mensual", sa.Numeric(18, 2), nullable=False),
        sa.Column("periodicidad", sa.String(20), nullable=False),
        sa.Column("moneda", sa.String(3), nullable=False),
        sa.Column("seleccionada_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("vigente_hasta", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.UniqueConstraint("cliente_id", "idempotency_key", name="uq_provisional_cliente_clave"),
        sa.CheckConstraint("revision >= 1", name="ck_provisional_revision"),
    )
    op.create_index(
        "uq_provisional_activa",
        "selecciones_provisionales",
        ["cotizacion_id"],
        unique=True,
        sqlite_where=sa.text("activa = 1"),
        postgresql_where=sa.text("activa = true"),
    )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(sa.text("LOCK TABLE selecciones_provisionales IN ACCESS EXCLUSIVE MODE"))
    else:
        connection.execute(
            sa.text("UPDATE selecciones_provisionales SET activa = activa WHERE 1=0")
        )
    if connection.scalar(sa.text("SELECT COUNT(*) FROM selecciones_provisionales")):
        raise RuntimeError(
            "Conserve las selecciones y sus claves al revertir codigo; no borre esta migracion."
        )
    op.drop_table("selecciones_provisionales")
