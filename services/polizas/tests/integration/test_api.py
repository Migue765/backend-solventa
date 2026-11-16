from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from flask import Flask
from polizas.app import create_app
from polizas.infrastructure.models import Base, PolizaModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

TOKEN = "polizas-test-internal"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    url = f"sqlite:///{tmp_path / 'polizas.db'}"
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    return create_app(url, TOKEN)


def test_api_persistente_idempotente_y_aislada(app: Flask) -> None:
    cliente = app.test_client()
    user = str(uuid4())
    evidencia = {
        "clienteId": user,
        "suscripcionId": str(uuid4()),
        "pagoId": str(uuid4()),
        "tipo": "automotive",
        "confirmadaEn": datetime.now(UTC).isoformat(),
        "simulada": True,
        "fechasContratadas": None,
    }
    assert cliente.post("/v1/polizas", json=evidencia).status_code == 401
    primera = cliente.post("/v1/polizas", json=evidencia, headers=HEADERS)
    assert primera.status_code == 200
    assert primera.get_json()["simulada"] is True
    assert primera.get_json()["estado"] == "vigente"
    assert (
        cliente.post("/v1/polizas", json=evidencia, headers=HEADERS).get_json()
        == primera.get_json()
    )
    assert (
        cliente.post(
            "/v1/polizas", json={**evidencia, "clienteId": str(uuid4())}, headers=HEADERS
        ).status_code
        == 409
    )
    assert (
        cliente.post(
            "/v1/polizas", json={**evidencia, "pagoId": str(uuid4())}, headers=HEADERS
        ).status_code
        == 409
    )
    assert cliente.get(
        "/v1/polizas", query_string={"clienteId": str(uuid4())}, headers=HEADERS
    ).get_json() == {"polizas": []}
    response = cliente.get("/v1/polizas", query_string={"clienteId": user}, headers=HEADERS)
    assert response.get_json() == {"polizas": [primera.get_json()]}
    assert response.headers["Cache-Control"] == "no-store"
    engine = app.extensions["polizas_engine"]
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(PolizaModel)) == 1
    restarted = create_app(str(engine.url), TOKEN).test_client()
    assert (
        restarted.get("/v1/polizas", query_string={"clienteId": user}, headers=HEADERS).get_json()
        == response.get_json()
    )


def test_migracion_coincide_con_modelos(app: Flask) -> None:
    with app.extensions["polizas_engine"].connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []


def test_emisiones_concurrentes_no_duplican(app: Flask) -> None:
    evidencia = {
        "clienteId": str(uuid4()),
        "suscripcionId": str(uuid4()),
        "pagoId": str(uuid4()),
        "tipo": "life",
        "confirmadaEn": "2026-10-19T00:00:00Z",
        "simulada": True,
        "fechasContratadas": None,
    }

    def emitir(_: int) -> str:
        with app.test_client() as client:
            response = client.post("/v1/polizas", json=evidencia, headers=HEADERS)
            assert response.status_code == 200, response.get_json()
            return str(response.get_json()["id"])

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert len(set(pool.map(emitir, range(8)))) == 1
    with Session(app.extensions["polizas_engine"]) as session:
        assert session.scalar(select(func.count()).select_from(PolizaModel)) == 1


@pytest.mark.parametrize("query", ["", "?clienteId=x", "?clienteId=x&clienteId=y", "?otro=x"])
def test_consulta_invalida(app: Flask, query: str) -> None:
    assert app.test_client().get("/v1/polizas" + query, headers=HEADERS).status_code == 400


def test_fallo_persistencia_no_parece_lista_vacia(tmp_path: Path) -> None:
    app = create_app(f"sqlite:///{tmp_path / 'sin-migrar.db'}", TOKEN)
    assert (
        app.test_client()
        .get("/v1/polizas", query_string={"clienteId": str(uuid4())}, headers=HEADERS)
        .status_code
        == 503
    )


def _emitir(app: Flask, tipo: str = "automotive", confirmada: str = "2026-10-01T00:00:00Z") -> dict:
    evidencia = {
        "clienteId": str(uuid4()),
        "suscripcionId": str(uuid4()),
        "pagoId": str(uuid4()),
        "tipo": tipo,
        "confirmadaEn": confirmada,
        "simulada": True,
        "fechasContratadas": None,
    }
    respuesta = app.test_client().post("/v1/polizas", json=evidencia, headers=HEADERS)
    assert respuesta.status_code == 200
    return {**evidencia, **respuesta.get_json()}


def test_reporte_por_corte_reproducible_y_sin_datos_del_cliente(app: Flask) -> None:
    cliente = app.test_client()
    primera = _emitir(app, "life")
    antes = datetime.now(UTC)
    corte = antes.isoformat()
    assert cliente.get("/v1/reportes/polizas", query_string={"corte": corte}).status_code == 401
    respuesta = cliente.get("/v1/reportes/polizas", query_string={"corte": corte}, headers=HEADERS)
    _emitir(app, "automotive")

    assert respuesta.status_code == 200
    cuerpo = respuesta.get_json()
    assert respuesta.headers["Cache-Control"] == "no-store"
    assert cuerpo["fuente"] == "polizas"
    assert cuerpo["registros"] == 1
    assert cuerpo["polizas"] == [
        {
            "id": primera["id"],
            "tipo": "life",
            "pagoId": primera["pagoId"],
            "emitidaEn": cuerpo["polizas"][0]["emitidaEn"],
            "fechaInicio": "2026-10-01",
            "fechaFin": "2026-11-01",
            "estado": "vigente" if antes.date() <= date(2026, 11, 1) else "vencida",
        }
    ]
    assert primera["clienteId"] not in respuesta.get_data(as_text=True)
    repetida = cliente.get("/v1/reportes/polizas", query_string={"corte": corte}, headers=HEADERS)
    assert repetida.get_json() == cuerpo
    actual = cliente.get(
        "/v1/reportes/polizas",
        query_string={"corte": datetime.now(UTC).isoformat()},
        headers=HEADERS,
    ).get_json()
    assert actual["registros"] == 2
    assert actual["huella"] != cuerpo["huella"]


def test_estado_del_reporte_se_calcula_al_corte(app: Flask) -> None:
    _emitir(app, "automotive", "2026-01-10T00:00:00Z")
    engine = app.extensions["polizas_engine"]
    with Session(engine) as session:
        poliza = session.scalars(select(PolizaModel)).one()
        poliza.emitida_en = datetime(2026, 1, 10, tzinfo=UTC)
        session.commit()
    respuesta = app.test_client().get(
        "/v1/reportes/polizas", query_string={"corte": "2026-01-20T00:00:00Z"}, headers=HEADERS
    )
    assert respuesta.get_json()["polizas"][0]["estado"] == "vigente"
    previa = app.test_client().get(
        "/v1/reportes/polizas", query_string={"corte": "2026-01-09T23:59:59Z"}, headers=HEADERS
    )
    assert previa.get_json()["registros"] == 0


@pytest.mark.parametrize(
    "query",
    [
        "",
        "?corte=2026-01-01T00:00:00",
        "?corte=x",
        "?corte=2999-01-01T00:00:00Z",
        "?corte=2026-01-01T00:00:00Z&corte=2026-01-02T00:00:00Z",
        "?corte=2026-01-01T00:00:00Z&clienteId=x",
    ],
)
def test_reporte_rechaza_corte_invalido(app: Flask, query: str) -> None:
    assert app.test_client().get("/v1/reportes/polizas" + query, headers=HEADERS).status_code == 400


def test_reporte_con_fallo_de_persistencia_no_parece_vacio(tmp_path: Path) -> None:
    app = create_app(f"sqlite:///{tmp_path / 'sin-migrar.db'}", TOKEN)
    respuesta = app.test_client().get(
        "/v1/reportes/polizas", query_string={"corte": "2026-01-01T00:00:00Z"}, headers=HEADERS
    )
    assert respuesta.status_code == 503
