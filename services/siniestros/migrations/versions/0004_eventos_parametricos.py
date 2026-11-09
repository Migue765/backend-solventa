"""Agrega recepcion, reglas, decisiones y outbox de eventos parametricos."""

import sqlalchemy as sa
from alembic import op

revision = "0004_eventos_parametricos"
down_revision = "0003_resultados_operacion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "estado_cola_parametrica",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("pendientes", sa.Integer(), nullable=False),
    )
    op.execute("INSERT INTO estado_cola_parametrica (id, pendientes) VALUES (1, 0)")
    op.create_table(
        "eventos_parametricos",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("fuente", sa.String(100), nullable=False),
        sa.Column("evento_id", sa.String(36), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("ocurrido_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recibido_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("estado", sa.String(20), nullable=False),
        sa.Column("procesado_en", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("estado IN ('pendiente', 'procesado')"),
        sa.UniqueConstraint("fuente", "evento_id"),
    )
    op.create_index(
        "ix_eventos_parametricos_estado_recibido",
        "eventos_parametricos",
        ["estado", "recibido_en"],
    )
    op.create_table(
        "reglas_parametricas",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("regla_id", sa.String(36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("fuente", sa.String(100), nullable=False),
        sa.Column("tipo_evento", sa.String(100), nullable=False),
        sa.Column("metrica", sa.String(100), nullable=False),
        sa.Column("operador", sa.String(8), nullable=False),
        sa.Column("umbral", sa.Numeric(18, 6), nullable=False),
        sa.Column("unidad", sa.String(30), nullable=False),
        sa.Column("ubicacion_codigo", sa.String(100), nullable=True),
        sa.Column("cliente_id", sa.String(36), nullable=False),
        sa.Column("poliza_id", sa.String(36), nullable=False),
        sa.Column("vigente_desde", sa.DateTime(timezone=True), nullable=False),
        sa.Column("vigente_hasta", sa.DateTime(timezone=True), nullable=True),
        sa.Column("creada_en", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version > 0"),
        sa.CheckConstraint("operador IN ('gt', 'gte', 'lt', 'lte', 'eq')"),
        sa.UniqueConstraint("regla_id", "version"),
    )
    op.create_index(
        "ix_reglas_parametricas_busqueda",
        "reglas_parametricas",
        ["fuente", "tipo_evento", "metrica", "unidad", "vigente_desde"],
    )
    op.create_table(
        "decisiones_parametricas",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "evento_id",
            sa.String(36),
            sa.ForeignKey("eventos_parametricos.id"),
            nullable=False,
        ),
        sa.Column("regla_id", sa.String(36), nullable=True),
        sa.Column("regla_version", sa.Integer(), nullable=True),
        sa.Column("decision", sa.String(20), nullable=False),
        sa.Column("decidida_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("siniestro_id", sa.String(36), sa.ForeignKey("siniestros.id"), nullable=True),
        sa.CheckConstraint("decision IN ('valido', 'no_valido', 'sin_regla_vigente')"),
        sa.UniqueConstraint("evento_id", "regla_id", "regla_version"),
    )
    op.create_index(
        "ix_decisiones_parametricas_evento_id",
        "decisiones_parametricas",
        ["evento_id"],
    )
    op.create_table(
        "casos_pago_automatico",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "evento_id",
            sa.String(36),
            sa.ForeignKey("eventos_parametricos.id"),
            nullable=False,
        ),
        sa.Column("regla_id", sa.String(36), nullable=False),
        sa.Column("regla_version", sa.Integer(), nullable=False),
        sa.Column(
            "siniestro_id",
            sa.String(36),
            sa.ForeignKey("siniestros.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("estado", sa.String(20), nullable=False),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("entregado_en", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("estado IN ('pendiente', 'entregado')"),
        sa.UniqueConstraint("evento_id", "regla_id", "regla_version"),
    )
    op.create_index(
        "ix_casos_pago_estado_creado",
        "casos_pago_automatico",
        ["estado", "creado_en"],
    )


def downgrade() -> None:
    op.drop_index("ix_casos_pago_estado_creado", table_name="casos_pago_automatico")
    op.drop_table("casos_pago_automatico")
    op.drop_index("ix_decisiones_parametricas_evento_id", table_name="decisiones_parametricas")
    op.drop_table("decisiones_parametricas")
    op.drop_index("ix_reglas_parametricas_busqueda", table_name="reglas_parametricas")
    op.drop_table("reglas_parametricas")
    op.drop_index("ix_eventos_parametricos_estado_recibido", table_name="eventos_parametricos")
    op.drop_table("eventos_parametricos")
    op.drop_table("estado_cola_parametrica")
