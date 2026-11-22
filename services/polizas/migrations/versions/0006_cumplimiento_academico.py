"""Reportes inmutables, intercambio academico y auditoria."""

import sqlalchemy as sa
from alembic import op

revision = "polizas_0006"
down_revision = "polizas_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reportes_regulatorios_academicos",
        sa.Column("reporte_id", sa.Uuid(), primary_key=True),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("contenido_hash", sa.String(64), nullable=False),
        sa.Column("contenido", sa.Text(), nullable=False),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("actor_id", "idempotency_key"),
    )
    op.create_table(
        "intercambios_reaseguro_academicos",
        sa.Column("intercambio_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "reporte_id",
            sa.Uuid(),
            sa.ForeignKey("reportes_regulatorios_academicos.reporte_id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("solicitud_hash", sa.String(64), nullable=False),
        sa.Column("contenido", sa.JSON(), nullable=False),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("actor_id", "idempotency_key"),
    )
    op.create_table(
        "auditoria_cumplimiento",
        sa.Column("auditoria_id", sa.Uuid(), primary_key=True),
        sa.Column("recurso_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("accion", sa.String(40), nullable=False),
        sa.Column("correlation_id", sa.String(64), nullable=False),
        sa.Column("ocurrido_en", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_auditoria_cumplimiento_recurso_id", "auditoria_cumplimiento", ["recurso_id"]
    )


def downgrade() -> None:
    op.drop_table("auditoria_cumplimiento")
    op.drop_table("intercambios_reaseguro_academicos")
    op.drop_table("reportes_regulatorios_academicos")
