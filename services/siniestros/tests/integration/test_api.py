import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from flask import Flask
from siniestros.app import create_app
from siniestros.infrastructure.models import Base, CambioEstadoModel, SiniestroModel
from sqlalchemy import update
from sqlalchemy.orm import Session

TOKEN = "siniestros-test-internal"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
REPORTE = datetime(2026, 11, 1, 15, 0, tzinfo=UTC)


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    url = f"sqlite:///{tmp_path / 'siniestros.db'}"
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    return create_app(url, TOKEN)


def registrar(app: Flask, cliente_id: UUID, cambios: list[tuple[str, timedelta, bool]]) -> UUID:
    siniestro_id = uuid4()
    with Session(app.extensions["siniestros_engine"]) as session, session.begin():
        session.add(
            SiniestroModel(
                siniestro_id=siniestro_id,
                cliente_id=cliente_id,
                poliza_id=uuid4(),
                tipo="asistido",
                estado=cambios[-1][0],
                ocurrido_en=REPORTE - timedelta(days=1),
                reportado_en=REPORTE,
                actualizado_en=REPORTE + cambios[-1][1],
            )
        )
        session.flush()
        session.add_all(
            CambioEstadoModel(
                siniestro_id=siniestro_id,
                secuencia=secuencia,
                estado=estado,
                ocurrido_en=REPORTE + desfase,
                visible_cliente=visible,
            )
            for secuencia, (estado, desfase, visible) in enumerate(cambios, start=1)
        )
    return siniestro_id


HISTORIAL = [
    ("reportado", timedelta(0), True),
    ("en_evaluacion", timedelta(hours=1), True),
    ("en_revision", timedelta(hours=2), False),
    ("aprobado", timedelta(hours=3), True),
]


def test_consulta_estado_vigente_e_historial_permitido(app: Flask) -> None:
    cliente_id = uuid4()
    siniestro_id = registrar(app, cliente_id, HISTORIAL)

    response = app.test_client().get(
        f"/v1/siniestros/{siniestro_id}",
        query_string={"clienteId": str(cliente_id)},
        headers=HEADERS,
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["id"] == str(siniestro_id)
    assert body["estado"] == "aprobado"
    assert body["tipo"] == "asistido"
    assert body["ocurridoEn"] == "2026-10-31T15:00:00Z"
    assert body["reportadoEn"] == "2026-11-01T15:00:00Z"
    assert body["actualizadoEn"] == "2026-11-01T18:00:00Z"
    assert [cambio["estado"] for cambio in body["historial"]] == [
        "reportado",
        "en_evaluacion",
        "aprobado",
    ]
    assert all(
        set(cambio) == {"estado", "descripcion", "ocurridoEn"} for cambio in body["historial"]
    )
    assert response.headers["Cache-Control"] == "no-store"


def test_siniestro_de_otro_cliente_responde_igual_que_inexistente(app: Flask) -> None:
    siniestro_id = registrar(app, uuid4(), HISTORIAL)
    cliente = app.test_client()

    ajeno = cliente.get(
        f"/v1/siniestros/{siniestro_id}",
        query_string={"clienteId": str(uuid4())},
        headers=HEADERS,
    )
    inexistente = cliente.get(
        f"/v1/siniestros/{uuid4()}",
        query_string={"clienteId": str(uuid4())},
        headers=HEADERS,
    )

    assert ajeno.status_code == inexistente.status_code == 404
    assert ajeno.get_json()["error"] == inexistente.get_json()["error"] == "siniestro_no_encontrado"
    assert set(ajeno.get_json()) == {"error", "correlationId"}


def test_requiere_credencial_interna(app: Flask) -> None:
    siniestro_id = registrar(app, uuid4(), HISTORIAL)

    response = app.test_client().get(
        f"/v1/siniestros/{siniestro_id}", query_string={"clienteId": str(uuid4())}
    )

    assert response.status_code == 401


@pytest.mark.parametrize(
    "ruta",
    [
        f"/v1/siniestros/{uuid4()}",
        f"/v1/siniestros/{uuid4()}?clienteId=x",
        f"/v1/siniestros/no-uuid?clienteId={uuid4()}",
        f"/v1/siniestros/{uuid4()}?clienteId={uuid4()}&clienteId={uuid4()}",
        f"/v1/siniestros/{uuid4()}?clienteId={uuid4()}&otro=x",
    ],
)
def test_consulta_invalida(app: Flask, ruta: str) -> None:
    assert app.test_client().get(ruta, headers=HEADERS).status_code == 400


def test_registro_incoherente_no_es_un_estado_valido(app: Flask) -> None:
    cliente_id = uuid4()
    siniestro_id = registrar(app, cliente_id, HISTORIAL)
    with Session(app.extensions["siniestros_engine"]) as session, session.begin():
        session.execute(update(SiniestroModel).values(estado="pagado"))

    response = app.test_client().get(
        f"/v1/siniestros/{siniestro_id}",
        query_string={"clienteId": str(cliente_id)},
        headers=HEADERS,
    )

    assert response.status_code == 503
    assert response.get_json()["error"] == "siniestro_no_integro"


def test_fallo_persistencia_no_parece_historial_vacio(tmp_path: Path) -> None:
    app = create_app(f"sqlite:///{tmp_path / 'sin-migrar.db'}", TOKEN)

    response = app.test_client().get(
        f"/v1/siniestros/{uuid4()}", query_string={"clienteId": str(uuid4())}, headers=HEADERS
    )

    assert response.status_code == 503
    assert response.get_json()["error"] == "servicio_no_disponible"


def test_migracion_coincide_con_modelos(app: Flask) -> None:
    with app.extensions["siniestros_engine"].connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []


def test_latencia_de_consulta(app: Flask) -> None:
    cliente_id = uuid4()
    siniestros = [registrar(app, cliente_id, HISTORIAL) for _ in range(20)]
    cliente = app.test_client()
    latencias_ms = []
    for indice in range(200):
        inicio = perf_counter()
        response = cliente.get(
            f"/v1/siniestros/{siniestros[indice % len(siniestros)]}",
            query_string={"clienteId": str(cliente_id)},
            headers=HEADERS,
        )
        latencias_ms.append((perf_counter() - inicio) * 1000)
        assert response.status_code == 200
    ordenadas = sorted(latencias_ms)

    assert ordenadas[189] <= 150
    assert ordenadas[197] <= 300


def test_siembra_local_produce_casos_consultables(app: Flask) -> None:
    ruta = Path(__file__).resolve().parents[2] / "scripts" / "sembrar_local.py"
    spec = importlib.util.spec_from_file_location("sembrar_local", ruta)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    cliente_id = uuid4()

    identificadores = modulo.sembrar(
        str(app.extensions["siniestros_engine"].url), cliente_id, REPORTE
    )

    estados = [
        app.test_client()
        .get(
            f"/v1/siniestros/{siniestro_id}",
            query_string={"clienteId": str(cliente_id)},
            headers=HEADERS,
        )
        .get_json()["estado"]
        for siniestro_id in identificadores
    ]
    assert estados == ["reportado", "en_inspeccion", "pagado"]
    with pytest.raises(ValueError, match="SQLite"):
        modulo.sembrar("postgresql://remoto/siniestros", cliente_id, REPORTE)
