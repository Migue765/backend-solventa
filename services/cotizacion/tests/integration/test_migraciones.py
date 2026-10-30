import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
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
    assert "suscripciones" not in tablas
    assert "decisiones_suscripcion" not in tablas
    assert "sagas_emision" not in tablas
    assert "pagos_simulados" not in tablas


def test_siembra_una_regla_inicial_para_cada_seguro(database_url):
    with create_engine(database_url).connect() as conexion:
        filas = conexion.execute(
            text("select version, tasa from reglas_tarifa where vigente_hasta is null")
        ).all()

    assert len(filas) == 15
    assert sum(fila.version == "tarifa-2026.1" for fila in filas) == 8
    assert sum(fila.version == "academico-v1" for fila in filas) == 7
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


@pytest.mark.parametrize(
    ("revision", "cantidad"),
    [("clima_v1", 8), ("academico_v1", 14), ("0006", 14), ("clima_academico", 15)],
)
def test_union_desde_cualquier_rama_conserva_reglas_y_decisiones(
    alembic_config, revision, cantidad
):
    from uuid import UUID

    from cotizacion.app import create_app
    from cotizacion.config import Config
    from cotizacion.infrastructure.persistence.database import crear_sesiones
    from cotizacion.infrastructure.persistence.repositorio_cotizaciones import (
        RepositorioCotizacionesSqlAlchemy,
    )

    from .test_api import solicitud

    script = ScriptDirectory.from_config(alembic_config)
    assert script.get_heads() == ["ofertas_hipotecarias"]
    assert script.get_revision("0006").down_revision == "academico_v1"
    command.upgrade(alembic_config, revision)
    url = alembic_config.get_main_option("sqlalchemy.url")
    with create_engine(url).connect() as conexion:
        reglas_antes = set(conexion.execute(text("select regla_id, version from reglas_tarifa")))
    assert len(reglas_antes) == cantidad
    cliente = create_app(Config(database_url=url)).test_client()
    respuesta = cliente.post("/v1/cotizaciones", json=solicitud())
    assert respuesta.status_code == 201
    repositorio = RepositorioCotizacionesSqlAlchemy(crear_sesiones(url))
    cotizacion_id = UUID(respuesta.get_json()["cotizacionId"])
    antes = repositorio.obtener(cotizacion_id)

    command.upgrade(alembic_config, "head")

    with create_engine(url).connect() as conexion:
        reglas_despues = set(conexion.execute(text("select regla_id, version from reglas_tarifa")))
        assert (
            conexion.scalar(text("select version_num from alembic_version"))
            == "ofertas_hipotecarias"
        )
    assert len(reglas_despues) == 15
    assert reglas_antes <= reglas_despues
    assert repositorio.obtener(cotizacion_id) == antes


def test_union_conserva_suscripcion_y_saga_de_develop(alembic_config):
    from uuid import uuid4

    from cotizacion.app import create_app
    from cotizacion.config import Config

    from .test_solicitudes_api import nueva_solicitud

    command.upgrade(alembic_config, "0006")
    database_url = alembic_config.get_main_option("sqlalchemy.url")
    cliente = create_app(Config(database_url=database_url)).test_client()
    payload = nueva_solicitud()
    respuesta = cliente.post(
        "/v1/solicitudes-cotizacion",
        json=payload,
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert respuesta.status_code == 201
    solicitud_id = respuesta.get_json()["id"]
    url = f"/v1/solicitudes-cotizacion/{solicitud_id}"
    query = {"clienteId": payload["clienteId"]}
    oferta = cliente.get(f"{url}/ofertas", query_string=query).get_json()["ofertas"][0]
    seleccion = cliente.put(
        f"{url}/seleccion",
        query_string=query,
        json={"ofertaId": oferta["id"], "version": oferta["version"]},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert seleccion.status_code == 200
    creada = cliente.post(
        f"{url}/suscripcion",
        query_string=query,
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert creada.status_code == 201
    repositorio = cliente.application.extensions["repositorio_cotizaciones"]
    antes = repositorio.obtener_suscripcion(solicitud_id)
    assert antes is not None

    command.upgrade(alembic_config, "head")

    despues = create_app(Config(database_url=database_url)).test_client()
    assert despues.get(f"{url}/suscripcion", query_string=query).get_json() == creada.get_json()
    assert despues.get(f"{url}/seleccion", query_string=query).get_json() == seleccion.get_json()
    assert (
        despues.application.extensions["repositorio_cotizaciones"].obtener_suscripcion(solicitud_id)
        == antes
    )
