"""Modificacion versionada de polizas con endosos y auditoria."""
import sqlalchemy as sa
from alembic import op

revision = "polizas_0003"
down_revision = "polizas_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "versiones_poliza",
        sa.Column("poliza_id", sa.Uuid(), primary_key=True),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("plan", sa.String(20), nullable=False),
        sa.Column("coberturas", sa.JSON(), nullable=False),
        sa.Column("prima", sa.String(20), nullable=False),
        sa.Column("efectivo_desde", sa.Date(), nullable=False),
        sa.Column("origen", sa.String(20), nullable=False),
        sa.Column("endoso_id", sa.Uuid(), nullable=True),
        sa.Column("creada_en", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "endosos",
        sa.Column("endoso_id", sa.Uuid(), primary_key=True),
        sa.Column("poliza_id", sa.Uuid(), nullable=False, index=True),
        sa.Column("cliente_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("solicitud_hash", sa.String(64), nullable=False),
        sa.Column("version_anterior", sa.Integer(), nullable=False),
        sa.Column("version_nueva", sa.Integer(), nullable=False),
        sa.Column("impacto", sa.JSON(), nullable=False),
        sa.Column("impacto_hash", sa.String(64), nullable=False),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("cliente_id", "idempotency_key"),
    )
    op.create_table(
        "auditoria_polizas",
        sa.Column("auditoria_id", sa.Uuid(), primary_key=True),
        sa.Column("poliza_id", sa.Uuid(), nullable=False, index=True),
        sa.Column("cliente_id", sa.Uuid(), nullable=False),
        sa.Column("accion", sa.String(40), nullable=False),
        sa.Column("resultado", sa.String(20), nullable=False),
        sa.Column("motivo", sa.String(60), nullable=True),
        sa.Column("detalle", sa.JSON(), nullable=False),
        sa.Column("correlation_id", sa.String(64), nullable=False),
        sa.Column("ocurrido_en", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("auditoria_polizas")
    op.drop_table("endosos")
    op.drop_table("versiones_poliza")
