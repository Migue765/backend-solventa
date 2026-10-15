"""agregar datos especificos del producto

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-14
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


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


def downgrade() -> None:
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
