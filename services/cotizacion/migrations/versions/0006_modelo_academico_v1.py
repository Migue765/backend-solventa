"""modelo academico v1 y periodicidad de seleccion

Revision ID: academico_v1
Revises: 0005
Create Date: 2026-10-17
"""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import sqlalchemy as sa
from alembic import op

revision = "academico_v1"
down_revision = "0005"
branch_labels = None
depends_on = None

VERSION = "academico-v1"
INICIO = datetime(2026, 10, 17, tzinfo=timezone.utc)
TASAS = (
    ("11111111-1111-4111-8111-111111111111", "0.0015"),
    ("33333333-3333-4333-8333-333333333333", "0.00001"),
    ("55555555-5555-4555-8555-555555555555", "0.001"),
    ("77777777-7777-4777-8777-777777777777", "0.004"),
    ("99999999-9999-4999-8999-999999999999", "0.01"),
    ("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "0.005"),
    ("dddddddd-dddd-4ddd-8ddd-dddddddddddd", "0.0005"),
)


def _reglas():
    return sa.table(
        "reglas_tarifa",
        sa.column("regla_id", sa.Uuid()),
        sa.column("version", sa.String()),
        sa.column("producto_id", sa.Uuid()),
        sa.column("tasa", sa.Numeric()),
        sa.column("factor_perfil", sa.Numeric()),
        sa.column("vigente_desde", sa.DateTime(timezone=True)),
        sa.column("vigente_hasta", sa.DateTime(timezone=True)),
    )


def upgrade() -> None:
    # No modificar primas, decisiones ni vigencias historicas.
    op.bulk_insert(
        _reglas(),
        [
            {
                "regla_id": UUID(f"bbbbbbbb-0000-4000-8000-{indice:012d}"),
                "version": VERSION,
                "producto_id": UUID(producto),
                "tasa": Decimal(tasa),
                "factor_perfil": Decimal("0.20"),
                "vigente_desde": INICIO,
                "vigente_hasta": None,
            }
            for indice, (producto, tasa) in enumerate(TASAS, start=1)
        ],
    )
    with op.batch_alter_table("selecciones_oferta") as batch:
        batch.alter_column(
            "prima_mensual",
            existing_type=sa.Numeric(10, 4),
            type_=sa.Numeric(18, 2),
            existing_nullable=False,
        )
        batch.add_column(
            sa.Column("periodicidad", sa.String(20), nullable=False, server_default="mensual")
        )


def downgrade() -> None:
    # No reducir precision: los resultados nuevos pueden exceder Numeric(10, 4).
    with op.batch_alter_table("selecciones_oferta") as batch:
        batch.drop_column("periodicidad")
    reglas = _reglas()
    op.execute(reglas.delete().where(reglas.c.version == VERSION))
