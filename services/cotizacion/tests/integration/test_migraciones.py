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

    assert len(filas) == 14
    assert sum(fila.version == "tarifa-2026.1" for fila in filas) == 7
    assert {float(fila.tasa) for fila in filas if fila.version == "academico-v1"} == {
        0.0015,
        0.00001,
        0.001,
        0.004,
        0.01,
        0.005,
        0.0005,
    }


def test_upgrade_conserva_calculo_y_decision_historicos(alembic_config, database_url):
    from uuid import UUID

    from cotizacion.app import create_app
    from cotizacion.config import Config
    from cotizacion.infrastructure.persistence.database import crear_sesiones
    from cotizacion.infrastructure.persistence.repositorio_cotizaciones import (
        RepositorioCotizacionesSqlAlchemy,
    )

    from .test_api import solicitud

    command.downgrade(alembic_config, "0005")
    cliente = create_app(Config(database_url=database_url)).test_client()
    respuesta = cliente.post("/v1/cotizaciones", json=solicitud())
    assert respuesta.status_code == 201
    registro = respuesta.get_json()
    assert registro["primaEstimada"]["monto"] == "28350.00"
    repositorio = RepositorioCotizacionesSqlAlchemy(crear_sesiones(database_url))
    antes = repositorio.obtener(UUID(registro["cotizacionId"]))

    command.upgrade(alembic_config, "head")

    despues = repositorio.obtener(UUID(registro["cotizacionId"]))
    assert despues == antes
    assert despues.instantanea.regla_version == "tarifa-2026.1"
