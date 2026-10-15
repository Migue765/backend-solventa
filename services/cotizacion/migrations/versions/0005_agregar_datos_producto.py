"""agregar datos especificos del producto

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-14
"""

import json
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

PRODUCTOS_NUEVOS = (
    UUID("33333333-3333-4333-8333-333333333333"),
    UUID("55555555-5555-4555-8555-555555555555"),
    UUID("77777777-7777-4777-8777-777777777777"),
    UUID("99999999-9999-4999-8999-999999999999"),
    UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
    UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
)


def upgrade() -> None:
    with op.batch_alter_table("cotizaciones") as batch:
        batch.add_column(sa.Column("datos_producto", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("tipo_documento", sa.String(20), nullable=True))

    cotizaciones = sa.table(
        "cotizaciones",
        sa.column("cotizacion_id", sa.Uuid()),
        sa.column("solicitud_id", sa.String()),
        sa.column("patente_vehiculo", sa.String()),
        sa.column("datos_producto", sa.JSON()),
        sa.column("tipo_documento", sa.String()),
    )
    conexion = op.get_bind()
    for cotizacion_id, patente in conexion.execute(
        sa.select(cotizaciones.c.cotizacion_id, cotizaciones.c.patente_vehiculo).where(
            cotizaciones.c.patente_vehiculo.is_not(None)
        )
    ):
        conexion.execute(
            cotizaciones.update()
            .where(cotizaciones.c.cotizacion_id == cotizacion_id)
            .values(datos_producto={"vehiclePlate": patente})
        )
    conexion.execute(
        cotizaciones.update()
        .where(cotizaciones.c.solicitud_id.is_not(None))
        .values(tipo_documento="cc")
    )

    with op.batch_alter_table("cotizaciones") as batch:
        batch.drop_column("patente_vehiculo")

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
                "regla_id": UUID(f"aaaaaaaa-0000-4000-8000-{indice:012d}"),
                "version": "tarifa-2026.1",
                "producto_id": producto_id,
                "tasa": Decimal("0.015"),
                "factor_perfil": Decimal("0.10"),
                "vigente_desde": datetime(2026, 1, 1, tzinfo=timezone.utc),
                "vigente_hasta": None,
            }
            for indice, producto_id in enumerate(PRODUCTOS_NUEVOS, start=2)
        ],
    )


def downgrade() -> None:
    reglas = sa.table(
        "reglas_tarifa",
        sa.column("producto_id", sa.Uuid()),
    )
    op.execute(reglas.delete().where(reglas.c.producto_id.in_(PRODUCTOS_NUEVOS)))

    with op.batch_alter_table("cotizaciones") as batch:
        batch.add_column(sa.Column("patente_vehiculo", sa.String(8), nullable=True))

    cotizaciones = sa.table(
        "cotizaciones",
        sa.column("cotizacion_id", sa.Uuid()),
        sa.column("patente_vehiculo", sa.String()),
        sa.column("datos_producto", sa.JSON()),
    )
    conexion = op.get_bind()
    for cotizacion_id, datos in conexion.execute(
        sa.select(cotizaciones.c.cotizacion_id, cotizaciones.c.datos_producto).where(
            cotizaciones.c.datos_producto.is_not(None)
        )
    ):
        if isinstance(datos, str):
            datos = json.loads(datos)
        patente = datos.get("vehiclePlate")
        if patente is not None:
            conexion.execute(
                cotizaciones.update()
                .where(cotizaciones.c.cotizacion_id == cotizacion_id)
                .values(patente_vehiculo=patente)
            )

    with op.batch_alter_table("cotizaciones") as batch:
        batch.drop_column("tipo_documento")
        batch.drop_column("datos_producto")
