"""Elegibilidad explicita de recuperacion academica; nunca rellena historicos."""

import sqlalchemy as sa
from alembic import op

revision = "conciliacion_emision"
down_revision = "intentos_pago_simulado"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conciliaciones_emision",
        sa.Column(
            "pago_id",
            sa.Uuid(),
            sa.ForeignKey("pagos_simulados.pago_id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("autorizada_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("estado", sa.String(20), nullable=False),
        sa.Column("intentos", sa.Integer(), nullable=False),
        sa.Column("proximo_intento_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_hasta", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ultimo_error", sa.String(80), nullable=True),
        sa.Column("completada_en", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "estado IN ('pendiente', 'en_proceso', 'completada', 'agotada')",
            name="ck_conciliacion_emision_estado",
        ),
        sa.CheckConstraint("intentos >= 0", name="ck_conciliacion_emision_intentos"),
    )
    op.create_index(
        "ix_conciliacion_emision_pendiente",
        "conciliaciones_emision",
        ["estado", "proximo_intento_en"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    # El chequeo y DROP deben excluir nuevas autorizaciones concurrentes.
    if connection.dialect.name == "postgresql":
        connection.execute(sa.text("LOCK TABLE conciliaciones_emision IN ACCESS EXCLUSIVE MODE"))
    else:
        connection.execute(
            sa.text("UPDATE conciliaciones_emision SET intentos = intentos WHERE 1 = 0")
        )
    if connection.scalar(
        sa.text("SELECT COUNT(*) FROM conciliaciones_emision WHERE estado != 'completada'")
    ):
        raise RuntimeError(
            "No se puede borrar elegibilidad pendiente o agotada. "
            "Deshabilite el recuperador y conserve la migracion al revertir codigo."
        )
    op.drop_table("conciliaciones_emision")
