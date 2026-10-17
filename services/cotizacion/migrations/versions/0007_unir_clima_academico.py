"""Unir las revisiones climatica y academica sin alterar datos.

Revision ID: clima_academico
Revises: clima_v1, academico_v1
"""

revision = "clima_academico"
down_revision = ("clima_v1", "academico_v1")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
