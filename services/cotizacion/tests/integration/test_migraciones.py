from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from cotizacion.infrastructure.persistence.models import Base
from sqlalchemy import create_engine, inspect, text


def test_migraciones_coinciden_con_los_modelos(database_url):
    with create_engine(database_url).connect() as conexion:
        diferencias = compare_metadata(MigrationContext.configure(conexion), Base.metadata)

    assert diferencias == []


def test_downgrade_elimina_las_tablas(alembic_config, database_url):
    command.downgrade(alembic_config, "base")

    tablas = inspect(create_engine(database_url)).get_table_names()
    assert "cotizaciones" not in tablas
    assert "coberturas_cotizadas" not in tablas


def test_siembra_una_regla_inicial_para_cada_seguro(database_url):
    with create_engine(database_url).connect() as conexion:
        filas = conexion.execute(
            text("select version, tasa from reglas_tarifa where vigente_hasta is null")
        ).all()

    assert len(filas) == 8
    assert {(fila.version, float(fila.tasa)) for fila in filas} == {("tarifa-2026.1", 0.015)}
