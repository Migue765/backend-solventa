"""Crea eventos, preferencias, notificaciones e intentos de entrega."""

import sqlalchemy as sa
from alembic import op

revision = "0001_notificaciones"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "eventos_notificacion",
        sa.Column("evento_id", sa.Uuid(), primary_key=True),
        sa.Column("tipo", sa.String(50), nullable=False),
        sa.Column("cliente_id", sa.Uuid(), nullable=False),
        sa.Column("ocurrido_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recibido_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("huella", sa.String(64), nullable=False),
        sa.Column("resultado", sa.String(20), nullable=False),
    )
    op.create_index("ix_eventos_notificacion_cliente", "eventos_notificacion", ["cliente_id"])
    op.create_table(
        "preferencias_notificacion",
        sa.Column("cliente_id", sa.Uuid(), primary_key=True),
        sa.Column("tipo", sa.String(30), primary_key=True),
        sa.Column("canal", sa.String(20), primary_key=True),
        sa.Column("habilitada", sa.Boolean(), nullable=False),
        sa.Column("actualizada_en", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "notificaciones",
        sa.Column("notificacion_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "evento_id",
            sa.Uuid(),
            sa.ForeignKey("eventos_notificacion.evento_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("cliente_id", sa.Uuid(), nullable=False),
        sa.Column("tipo", sa.String(30), nullable=False),
        sa.Column("canal", sa.String(20), nullable=False),
        sa.Column("titulo", sa.String(80), nullable=False),
        sa.Column("contenido", sa.String(240), nullable=False),
        sa.Column("recurso_tipo", sa.String(20), nullable=False),
        sa.Column("recurso_id", sa.Uuid(), nullable=False),
        sa.Column("programada_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("estado", sa.String(20), nullable=False),
        sa.Column("intentos", sa.Integer(), nullable=False),
        sa.Column("proximo_intento_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("entregada_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ultimo_error", sa.String(80), nullable=True),
        sa.Column("reserva_id", sa.Uuid(), nullable=True),
        sa.Column("creada_en", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("evento_id", "tipo", "canal", name="uq_notificacion_evento_tipo_canal"),
        sa.CheckConstraint("intentos >= 0", name="ck_notificacion_intentos"),
    )
    op.create_index(
        "ix_notificaciones_cliente_entrega",
        "notificaciones",
        ["cliente_id", "canal", "entregada_en"],
    )
    op.create_index(
        "ix_notificaciones_pendientes",
        "notificaciones",
        ["estado", "proximo_intento_en"],
    )
    op.create_table(
        "intentos_entrega_notificacion",
        sa.Column("intento_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "notificacion_id",
            sa.Uuid(),
            sa.ForeignKey("notificaciones.notificacion_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("intentado_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resultado", sa.String(20), nullable=False),
        sa.Column("error", sa.String(80), nullable=True),
    )
    op.create_index(
        "ix_intentos_entrega_notificacion",
        "intentos_entrega_notificacion",
        ["notificacion_id", "intentado_en"],
    )


def downgrade() -> None:
    op.drop_table("intentos_entrega_notificacion")
    op.drop_table("notificaciones")
    op.drop_table("preferencias_notificacion")
    op.drop_table("eventos_notificacion")
