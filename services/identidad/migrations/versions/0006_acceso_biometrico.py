"""Credenciales de instalacion y desafios de acceso; sin datos biometricos."""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "credenciales_biometricas",
        sa.Column("credencial_id", sa.Uuid(), primary_key=True),
        sa.Column("usuario_id", sa.Uuid(), sa.ForeignKey("usuarios.usuario_id"), nullable=False),
        sa.Column("clave_publica", sa.LargeBinary(91), nullable=False),
        sa.Column("clave_hash", sa.String(43), unique=True, nullable=False),
        sa.Column("creada_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ultimo_uso_en", sa.DateTime(timezone=True)),
        sa.Column("revocada_en", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_credenciales_biometricas_usuario_id", "credenciales_biometricas", ["usuario_id"])
    op.create_table(
        "desafios_biometricos",
        sa.Column("desafio_id", sa.String(43), primary_key=True),
        sa.Column("credencial_id", sa.Uuid(), nullable=False),
        sa.Column("usuario_id", sa.Uuid(), sa.ForeignKey("usuarios.usuario_id"), nullable=False),
        sa.Column("proposito", sa.String(10), nullable=False),
        sa.Column("clave_publica", sa.LargeBinary(91), nullable=False),
        sa.Column("mensaje", sa.LargeBinary(512), nullable=False),
        sa.Column("creada_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expira_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumida_en", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_desafios_biometricos_credencial_id", "desafios_biometricos", ["credencial_id"])
    op.create_index("ix_desafios_biometricos_expira_en", "desafios_biometricos", ["expira_en"])
    op.create_table(
        "auditoria_biometrica",
        sa.Column("evento_id", sa.Uuid(), primary_key=True),
        sa.Column("creada_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("correlation_id", sa.String(36), nullable=False),
        sa.Column("operacion", sa.String(20), nullable=False),
        sa.Column("resultado", sa.String(40), nullable=False),
        sa.Column("usuario_id", sa.Uuid()),
        sa.Column("credencial_id", sa.Uuid()),
    )
    with op.batch_alter_table("refresh_tokens") as batch:
        batch.add_column(sa.Column("credencial_biometrica_id", sa.Uuid()))
        batch.create_foreign_key(
            "fk_refresh_credencial_biometrica", "credenciales_biometricas",
            ["credencial_biometrica_id"], ["credencial_id"],
        )
        batch.create_index("ix_refresh_tokens_credencial_biometrica_id", ["credencial_biometrica_id"])


def downgrade() -> None:
    with op.batch_alter_table("refresh_tokens") as batch:
        batch.drop_index("ix_refresh_tokens_credencial_biometrica_id")
        batch.drop_constraint("fk_refresh_credencial_biometrica", type_="foreignkey")
        batch.drop_column("credencial_biometrica_id")
    op.drop_table("auditoria_biometrica")
    op.drop_table("desafios_biometricos")
    op.drop_table("credenciales_biometricas")
