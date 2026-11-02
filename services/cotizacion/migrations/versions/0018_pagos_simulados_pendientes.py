"""Reservas durables y efectos del simulador, sin reinterpretar historicos."""

import sqlalchemy as sa
from alembic import op

revision = "pagos_simulados_pendientes"
down_revision = "selecciones_provisionales"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    duplicados = connection.scalar(
        sa.text(
            "SELECT COUNT(*) FROM (SELECT suscripcion_id FROM intentos_pago_simulado "
            "WHERE resultado = 'confirmado' GROUP BY suscripcion_id HAVING COUNT(*) > 1) "
            "AS duplicados"
        )
    )
    if duplicados:
        raise RuntimeError("Hay intentos confirmados duplicados; la migracion no elimina datos.")
    with op.batch_alter_table("intentos_pago_simulado") as batch:
        batch.add_column(sa.Column("creada_en", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("huella", sa.String(64), nullable=True))
        batch.add_column(
            sa.Column("emision_solicitada", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(
            sa.Column(
                "conciliacion_emision_solicitada",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(
            sa.Column("intentos_conciliacion", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(sa.Column("proximo_intento_en", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("ultimo_error", sa.String(80), nullable=True))
        batch.add_column(sa.Column("lease_token", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("lease_hasta", sa.DateTime(timezone=True), nullable=True))
    connection.execute(
        sa.text(
            "UPDATE intentos_pago_simulado "
            "SET creada_en = evaluado_en, proximo_intento_en = evaluado_en"
        )
    )
    with op.batch_alter_table("intentos_pago_simulado") as batch:
        batch.alter_column("creada_en", existing_type=sa.DateTime(timezone=True), nullable=False)
        batch.alter_column(
            "proximo_intento_en", existing_type=sa.DateTime(timezone=True), nullable=False
        )
        batch.alter_column("evaluado_en", existing_type=sa.DateTime(timezone=True), nullable=True)
        batch.drop_constraint("ck_intentos_pago_resultado", type_="check")
        batch.create_check_constraint(
            "ck_intentos_pago_resultado", "resultado IN ('pendiente', 'confirmado', 'rechazado')"
        )
        batch.create_check_constraint(
            "ck_intentos_pago_evaluacion",
            "(resultado = 'pendiente' AND evaluado_en IS NULL AND huella IS NOT NULL) OR "
            "(resultado IN ('confirmado', 'rechazado') AND evaluado_en IS NOT NULL)",
        )
        batch.create_check_constraint("ck_intentos_pago_conciliacion", "intentos_conciliacion >= 0")
        batch.create_check_constraint(
            "ck_intentos_pago_lease",
            "(lease_token IS NULL AND lease_hasta IS NULL) OR "
            "(lease_token IS NOT NULL AND lease_hasta IS NOT NULL)",
        )
        batch.create_check_constraint(
            "ck_intentos_pago_emision", "NOT conciliacion_emision_solicitada OR emision_solicitada"
        )
    op.create_index(
        "uq_intentos_pago_activo",
        "intentos_pago_simulado",
        ["suscripcion_id"],
        unique=True,
        sqlite_where=sa.text("resultado IN ('pendiente', 'confirmado')"),
        postgresql_where=sa.text("resultado IN ('pendiente', 'confirmado')"),
    )
    op.create_index(
        "ix_intentos_pago_conciliacion",
        "intentos_pago_simulado",
        ["resultado", "proximo_intento_en"],
    )
    op.create_table(
        "efectos_pago_simulado",
        sa.Column(
            "intento_id",
            sa.Uuid(),
            sa.ForeignKey("intentos_pago_simulado.intento_id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("huella", sa.String(64), nullable=False),
        sa.Column("resultado", sa.String(20), nullable=False),
        sa.Column("evaluado_en", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "resultado IN ('confirmado', 'rechazado')", name="ck_efecto_pago_resultado"
        ),
    )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(
            sa.text(
                "LOCK TABLE intentos_pago_simulado, efectos_pago_simulado IN ACCESS EXCLUSIVE MODE"
            )
        )
    else:
        connection.execute(
            sa.text("UPDATE intentos_pago_simulado SET resultado = resultado WHERE 1=0")
        )
    if connection.scalar(
        sa.text("SELECT COUNT(*) FROM efectos_pago_simulado")
    ) or connection.scalar(
        sa.text(
            "SELECT COUNT(*) FROM intentos_pago_simulado "
            "WHERE resultado = 'pendiente' OR huella IS NOT NULL"
        )
    ):
        raise RuntimeError("Conserve reservas y efectos del simulador al revertir codigo.")
    op.drop_table("efectos_pago_simulado")
    op.drop_index("uq_intentos_pago_activo", table_name="intentos_pago_simulado")
    op.drop_index("ix_intentos_pago_conciliacion", table_name="intentos_pago_simulado")
    with op.batch_alter_table("intentos_pago_simulado") as batch:
        for constraint in (
            "ck_intentos_pago_resultado",
            "ck_intentos_pago_evaluacion",
            "ck_intentos_pago_conciliacion",
            "ck_intentos_pago_lease",
            "ck_intentos_pago_emision",
        ):
            batch.drop_constraint(constraint, type_="check")
        batch.create_check_constraint(
            "ck_intentos_pago_resultado", "resultado IN ('confirmado', 'rechazado')"
        )
        batch.alter_column("evaluado_en", existing_type=sa.DateTime(timezone=True), nullable=False)
        for column in (
            "creada_en",
            "huella",
            "emision_solicitada",
            "conciliacion_emision_solicitada",
            "intentos_conciliacion",
            "proximo_intento_en",
            "ultimo_error",
            "lease_token",
            "lease_hasta",
        ):
            batch.drop_column(column)
