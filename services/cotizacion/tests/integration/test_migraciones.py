from contextlib import ExitStack
from unittest.mock import patch

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from cotizacion.app import create_app
from cotizacion.config import Config
from cotizacion.infrastructure.conciliacion_emision import ConciliadorEmision
from cotizacion.infrastructure.persistence.models import Base
from sqlalchemy import create_engine, inspect, text


def cliente_historico(database_url):
    # El fixture reproduce una version anterior al lifecycle financiero nuevo.
    with patch.object(ConciliadorEmision, "iniciar"):
        app = create_app(Config(database_url=database_url))
    app.extensions["conciliador_emision"].cerrar()
    return app.test_client()


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
    assert "intentos_pago_simulado" not in tablas
    assert "efectos_pago_simulado" not in tablas
    assert "conciliaciones_emision" not in tablas
    assert "selecciones_provisionales" not in tablas


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

    from cotizacion.infrastructure.persistence.database import crear_sesiones
    from cotizacion.infrastructure.persistence.repositorio_cotizaciones import (
        RepositorioCotizacionesSqlAlchemy,
    )

    from .test_api import solicitud

    command.downgrade(alembic_config, "0005")
    cliente = cliente_historico(database_url)
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

    from cotizacion.infrastructure.persistence.database import crear_sesiones
    from cotizacion.infrastructure.persistence.repositorio_cotizaciones import (
        RepositorioCotizacionesSqlAlchemy,
    )

    from .test_api import solicitud

    script = ScriptDirectory.from_config(alembic_config)
    assert script.get_heads() == ["pagos_simulados_pendientes"]
    assert script.get_revision("0006").down_revision == "academico_v1"
    command.upgrade(alembic_config, revision)
    url = alembic_config.get_main_option("sqlalchemy.url")
    with create_engine(url).connect() as conexion:
        reglas_antes = set(conexion.execute(text("select regla_id, version from reglas_tarifa")))
    assert len(reglas_antes) == cantidad
    cliente = cliente_historico(url)
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
            == "pagos_simulados_pendientes"
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
    cliente = cliente_historico(database_url)
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
    # El codigo actual requiere la migracion nueva; sembramos la aceptacion del esquema 0006.
    import hashlib
    from datetime import UTC, datetime
    from decimal import Decimal
    from uuid import UUID

    from cotizacion.infrastructure.persistence.models import CotizacionModel, SeleccionOfertaModel
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    clave_aceptacion = uuid4()
    fingerprint_aceptacion = hashlib.sha256(
        f"{oferta['id']}:{oferta['version']}".encode()
    ).hexdigest()
    motor_historico = create_engine(database_url)
    with ExitStack() as recursos, Session(motor_historico) as sesion, sesion.begin():
        recursos.callback(motor_historico.dispose)
        cotizacion = sesion.scalar(
            select(CotizacionModel).where(CotizacionModel.solicitud_id == solicitud_id)
        )
        assert cotizacion is not None
        sesion.add(
            SeleccionOfertaModel(
                cotizacion_id=cotizacion.cotizacion_id,
                solicitud_id=solicitud_id,
                oferta_id=UUID(oferta["id"]),
                version=oferta["version"],
                nombre_oferta=oferta["nombre"],
                prima_mensual=Decimal(oferta["prima"]["monto"]),
                moneda=oferta["prima"]["moneda"],
                periodicidad=oferta["periodicidad"],
                seleccionada_en=datetime.now(UTC),
                idempotency_key=clave_aceptacion,
                request_fingerprint=fingerprint_aceptacion,
            )
        )
    seleccion = cliente.get(f"{url}/seleccion", query_string=query)
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
    replay = despues.put(
        f"{url}/seleccion",
        query_string=query,
        json={"ofertaId": oferta["id"], "version": oferta["version"]},
        headers={"Idempotency-Key": str(clave_aceptacion)},
    )
    assert replay.status_code == 200 and replay.get_json() == seleccion.get_json()
    assert (
        despues.application.extensions["repositorio_cotizaciones"].obtener_suscripcion(solicitud_id)
        == antes
    )


def test_migracion_provisional_no_reescribe_historia_y_protege_rollback(
    client, alembic_config, database_url
):
    from uuid import uuid4

    from .test_seleccion_provisional import elegir, preparar

    url, query, ofertas = preparar(client)
    aceptada = client.put(
        f"{url}/seleccion",
        query_string=query,
        json={"ofertaId": ofertas[0]["id"], "version": 1},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert aceptada.status_code == 200
    assert (
        client.post(
            f"{url}/suscripcion", query_string=query, headers={"Idempotency-Key": str(uuid4())}
        ).status_code
        == 201
    )
    client.application.extensions["conciliador_emision"].cerrar()
    command.downgrade(alembic_config, "conciliacion_emision")
    motor = create_engine(database_url)

    def historia():
        with motor.connect() as connection:
            return {
                tabla: sorted(connection.execute(text(f'SELECT * FROM "{tabla}"')).all(), key=repr)
                for tabla in inspect(connection).get_table_names()
                if tabla
                not in {"alembic_version", "selecciones_provisionales", "efectos_pago_simulado"}
            }

    try:
        antes = historia()
        command.upgrade(alembic_config, "head")
        assert historia() == antes
        with motor.connect() as connection:
            assert connection.scalar(text("SELECT COUNT(*) FROM selecciones_provisionales")) == 0
            assert connection.scalar(text("SELECT COUNT(*) FROM efectos_pago_simulado")) == 0
        assert client.get(f"{url}/seleccion", query_string=query).get_json() == aceptada.get_json()
        otro_url, otro_query, otras = preparar(client)
        assert elegir(client, otro_url, otro_query, otras[0], 0).status_code == 200
        with pytest.raises(RuntimeError, match="Conserve las selecciones"):
            command.downgrade(alembic_config, "conciliacion_emision")
        assert (
            client.get(f"{otro_url}/seleccion-provisional", query_string=otro_query).status_code
            == 200
        )
    finally:
        motor.dispose()
