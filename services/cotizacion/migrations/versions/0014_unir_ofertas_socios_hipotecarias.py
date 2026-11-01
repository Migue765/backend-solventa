"""Unifica las migraciones paralelas de ofertas de socios."""

revision = "ofertas_socios_hipotecarias"
down_revision = ("ofertas_socios", "ofertas_hipotecarias")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
