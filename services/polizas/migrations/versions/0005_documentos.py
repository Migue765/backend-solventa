"""Documentos academicos inmutables y constancias de aceptacion simulada."""
import sqlalchemy as sa
from alembic import op

revision = "polizas_0005"
down_revision = "polizas_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "documentos_poliza",
        sa.Column("documento_id", sa.Uuid(), primary_key=True),
        sa.Column("poliza_id", sa.Uuid(), sa.ForeignKey("polizas.poliza_id"), nullable=False),
        sa.Column("version_poliza", sa.Integer(), nullable=True),
        sa.Column("contenido_hash", sa.String(64), nullable=False),
        sa.Column("contenido", sa.Text(), nullable=False),
        sa.Column("archivo", sa.Text(), nullable=False),
        sa.Column("creada_en", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("poliza_id", "contenido_hash"),
    )
    op.create_index("ix_documentos_poliza_poliza_id", "documentos_poliza", ["poliza_id"])
    op.create_table(
        "aceptaciones_documento",
        sa.Column("aceptacion_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "documento_id", sa.Uuid(),
            sa.ForeignKey("documentos_poliza.documento_id"), nullable=False,
        ),
        sa.Column("poliza_id", sa.Uuid(), sa.ForeignKey("polizas.poliza_id"), nullable=False),
        sa.Column("cliente_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("solicitud_hash", sa.String(64), nullable=False),
        sa.Column("aceptada_en", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("cliente_id", "idempotency_key"),
    )
    op.create_index("ix_aceptaciones_documento_poliza_id", "aceptaciones_documento", ["poliza_id"])


def downgrade() -> None:
    op.drop_table("aceptaciones_documento")
    op.drop_table("documentos_poliza")
