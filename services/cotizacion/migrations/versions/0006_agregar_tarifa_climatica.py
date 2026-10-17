"""Agregar la tarifa simulada del seguro climatico.

Revision ID: clima_v1
Revises: 0005
"""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import sqlalchemy as sa
from alembic import op

revision = "clima_v1"
down_revision = "0005"
branch_labels = None
depends_on = None

REGLA_ID = UUID("aaaaaaaa-0000-4000-8000-000000000008")
PRODUCTO_ID = UUID("ffffffff-ffff-4fff-8fff-ffffffffffff")


def upgrade() -> None:
    reglas = sa.table(
        "reglas_tarifa",
        sa.column("regla_id", sa.Uuid()),
        sa.column("version", sa.String()),
        sa.column("producto_id", sa.Uuid()),
        sa.column("tasa", sa.Numeric()),
        sa.column("factor_perfil", sa.Numeric()),
        sa.column("vigente_desde", sa.DateTime(timezone=True)),
        sa.column("vigente_hasta", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        reglas,
        [
            {
                "regla_id": REGLA_ID,
                "version": "tarifa-2026.1",
                "producto_id": PRODUCTO_ID,
                "tasa": Decimal("0.015"),
                "factor_perfil": Decimal("0.10"),
                "vigente_desde": datetime(2026, 1, 1, tzinfo=timezone.utc),
                "vigente_hasta": None,
            }
        ],
    )


def downgrade() -> None:
    reglas = sa.table("reglas_tarifa", sa.column("regla_id", sa.Uuid()))
    op.execute(reglas.delete().where(reglas.c.regla_id == REGLA_ID))
