from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
import yaml
from alembic import command
from alembic.config import Config
from flask import Flask
from flask.testing import FlaskClient
from siniestros.app import create_app
from siniestros.domain import Config as ClaimsConfig
from siniestros.infrastructure import evaluacion as infraestructura
from siniestros.infrastructure.models import CambioEstadoModel
from siniestros.models import Claim, Upload
from siniestros.repository import fingerprint
from sqlalchemy import select
from sqlalchemy.orm import Session

TOKEN = "siniestros-test-internal"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
REPORTE = datetime(2026, 11, 1, 15, 0, tzinfo=UTC)
CONTRATO = Path(__file__).resolve().parents[4] / (
    "contracts/openapi/evaluacion-siniestros-internal-v1.yaml"
)
REGLAS: dict[str, Any] = {
    "version": 1,
    "vigenteDesde": "2026-01-01T00:00:00Z",
    "evidenciaMinima": 1,
    "tratamientoPorDefecto": "aprobacion",
    "motivoPorDefecto": "Cumple las condiciones para aprobacion automatica.",
    "reglas": [
        {
            "codigo": "REPORTE_TARDIO",
            "campo": "diasHastaReporte",
            "operador": "gt",
            "valor": 30,
            "tratamiento": "inspeccion",
            "motivo": "El reporte supera 30 dias desde el evento.",
        },
        {
            "codigo": "SIN_UBICACION",
            "campo": "cantidadEvidenciasConUbicacion",
            "operador": "eq",
            "valor": 0,
            "tratamiento": "revision_humana",
            "motivo": "Ninguna evidencia tiene ubicacion.",
        },
    ],
}


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    url = f"sqlite:///{tmp_path / 'siniestros.db'}"
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    return create_app(ClaimsConfig(url, tmp_path / "evidencias", TOKEN, "http://polizas.test"))


@pytest.fixture
def client(app: Flask) -> Iterator[FlaskClient]:
    yield app.test_client()


def registrar(
    app: Flask,
    dias: int = 1,
    tipo: str | None = None,
    estado: str = "reportado",
    evidencias: int = 1,
    ubicacion: bool = True,
) -> str:
    siniestro_id = str(uuid4())
    payload = {
        "polizaId": str(uuid4()),
        "fechaEvento": (REPORTE - timedelta(days=dias)).isoformat(),
        "tipoEvento": "Accidente",
        "descripcion": "Descripcion sintetica del evento.",
        "lugar": "Lugar de prueba",
    }
    with Session(app.extensions["siniestros_engine"]) as session, session.begin():
        session.add(
            Claim(
                id=siniestro_id,
                owner=str(uuid4()),
                operation=str(uuid4()),
                fingerprint=fingerprint(payload),
                payload=payload,
                created=REPORTE.isoformat(),
                tipo=tipo,
                estado=estado,
                actualizado_en=REPORTE,
            )
        )
        session.flush()
        session.add(
            CambioEstadoModel(
                siniestro_id=siniestro_id,
                secuencia=1,
                estado="reportado",
                ocurrido_en=REPORTE,
                visible_cliente=True,
            )
        )
        if estado != "reportado":
            session.add(
                CambioEstadoModel(
                    siniestro_id=siniestro_id,
                    secuencia=2,
                    estado=estado,
                    ocurrido_en=REPORTE + timedelta(hours=1),
                    visible_cliente=True,
                )
            )
    for _ in range(evidencias):
        confirmar_evidencia(app, siniestro_id, ubicacion)
    return siniestro_id


def confirmar_evidencia(app: Flask, siniestro_id: str, ubicacion: bool = True) -> None:
    with Session(app.extensions["siniestros_engine"]) as session, session.begin():
        session.add(
            Upload(
                id=str(uuid4()),
                claim_id=siniestro_id,
                owner=str(uuid4()),
                operation=str(uuid4()),
                fingerprint="0" * 64,
                mime="image/jpeg",
                size=10,
                digest="a" * 64,
                location={"lat": 4.6, "lon": -74.1} if ubicacion else None,
                captured=None,
                offset=10,
                status="confirmada",
                created=REPORTE.isoformat(),
                confirmed=REPORTE.isoformat(),
            )
        )


def publicar_reglas(client: FlaskClient, **cambios: Any) -> None:
    response = client.post(
        "/internal/v1/reglas-evaluacion-siniestros", json={**REGLAS, **cambios}, headers=HEADERS
    )
    assert response.status_code == 201, response.get_json()


def evaluar(client: FlaskClient, siniestro_id: str, cuerpo: dict[str, Any] | None = None) -> Any:
    return client.post(
        f"/internal/v1/siniestros/{siniestro_id}/evaluacion", json=cuerpo or {}, headers=HEADERS
    )


def historial_estados(app: Flask, siniestro_id: str) -> list[str]:
    with Session(app.extensions["siniestros_engine"]) as session:
        return list(
            session.scalars(
                select(CambioEstadoModel.estado)
                .where(CambioEstadoModel.siniestro_id == siniestro_id)
                .order_by(CambioEstadoModel.secuencia)
            )
        )


def test_evaluacion_aplica_reglas_y_deja_el_caso_en_evaluacion(
    app: Flask, client: FlaskClient
) -> None:
    publicar_reglas(client)
    siniestro_id = registrar(app)

    response = evaluar(client, siniestro_id)

    assert response.status_code == 201
    body = response.get_json()
    assert body["resultado"] == "decidido"
    assert body["tratamiento"] == "aprobacion"
    assert body["reglasVersion"] == 1
    assert body["estadoAnterior"] == "reportado"
    assert body["estadoResultante"] == "en_evaluacion"
    assert body["decisionAutomatica"] == {
        "evaluacionId": body["evaluacionId"],
        "evaluadoEn": body["evaluadaEn"],
        "elegible": True,
        "requiereInspeccion": False,
        "requiereRevisionHumana": False,
        "fraude": {"nivel": "bajo", "bloqueante": False},
        "reglas": [],
        "motivo": "Cumple las condiciones para aprobacion automatica.",
    }
    assert historial_estados(app, siniestro_id) == ["reportado", "en_evaluacion"]


def test_reglas_aplicadas_y_motivo_quedan_trazados(app: Flask, client: FlaskClient) -> None:
    publicar_reglas(client)
    siniestro_id = registrar(app, dias=45)

    body = evaluar(client, siniestro_id).get_json()

    assert body["tratamiento"] == "inspeccion"
    assert body["reglasAplicadas"] == [
        {
            "codigo": "REPORTE_TARDIO",
            "tratamiento": "inspeccion",
            "motivo": "El reporte supera 30 dias desde el evento.",
        }
    ]
    assert body["motivo"] == "El reporte supera 30 dias desde el evento."
    assert body["entradas"]["diasHastaReporte"] == 45
    assert body["entradas"]["longitudDescripcion"] == len("Descripcion sintetica del evento.")
    assert "descripcion" not in body["entradas"]
    assert body["decisionAutomatica"]["requiereInspeccion"] is True
    assert body["decisionAutomatica"]["reglas"] == [
        {"reglaId": "REPORTE_TARDIO", "version": 1, "resultado": "inspeccion"}
    ]
    historial = client.get(
        f"/internal/v1/siniestros/{siniestro_id}/evaluaciones", headers=HEADERS
    ).get_json()
    assert historial["evaluaciones"] == [body]


def test_reevaluar_mismas_entradas_y_version_es_determinista(
    app: Flask, client: FlaskClient
) -> None:
    publicar_reglas(client)
    siniestro_id = registrar(app)

    primera = evaluar(client, siniestro_id)
    segunda = evaluar(client, siniestro_id)

    assert (primera.status_code, segunda.status_code) == (201, 200)
    assert segunda.get_json() == primera.get_json()
    assert historial_estados(app, siniestro_id) == ["reportado", "en_evaluacion"]


def test_informacion_insuficiente_no_decide_ni_cambia_estado(
    app: Flask, client: FlaskClient
) -> None:
    publicar_reglas(client, evidenciaMinima=2)
    siniestro_id = registrar(app, evidencias=1)

    body = evaluar(client, siniestro_id).get_json()

    assert body["resultado"] == "informacion_insuficiente"
    assert body["tratamiento"] is None
    assert body["decisionAutomatica"] is None
    assert "al menos 2 evidencias" in body["motivo"]
    assert body["estadoResultante"] == "reportado"
    assert historial_estados(app, siniestro_id) == ["reportado"]

    confirmar_evidencia(app, siniestro_id)
    completada = evaluar(client, siniestro_id)
    assert completada.status_code == 201
    assert completada.get_json()["resultado"] == "decidido"


def test_regla_de_revision_humana_queda_marcada(app: Flask, client: FlaskClient) -> None:
    publicar_reglas(client)
    siniestro_id = registrar(app, ubicacion=False)

    body = evaluar(client, siniestro_id).get_json()

    assert body["tratamiento"] == "revision_humana"
    assert body["requiereRevisionHumana"] is True
    assert body["decisionAutomatica"]["requiereRevisionHumana"] is True
    assert body["estadoResultante"] == "en_evaluacion"


def test_senales_de_fraude_forman_parte_de_la_evaluacion(app: Flask, client: FlaskClient) -> None:
    publicar_reglas(client)
    siniestro_id = registrar(app)

    body = evaluar(
        client,
        siniestro_id,
        {"senalesFraude": [{"codigo": "EVIDENCIA_DUPLICADA", "nivel": "alto", "fuente": "fraude"}]},
    ).get_json()

    assert body["tratamiento"] == "revision_humana"
    assert body["reglasAplicadas"][0]["codigo"] == "FRAUDE_NIVEL"
    assert body["entradas"]["senalesFraudeDisponibles"] is True
    assert body["entradas"]["nivelFraude"] == 3
    assert body["decisionAutomatica"]["fraude"] == {"nivel": "alto", "bloqueante": True}


def test_fallo_del_evaluador_deja_el_caso_pendiente_sin_aprobar(
    app: Flask, client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    publicar_reglas(client)
    siniestro_id = registrar(app)

    def fallar(*_: Any) -> None:
        raise RuntimeError("motor no disponible")

    monkeypatch.setattr(infraestructura, "evaluar", fallar)
    body = evaluar(client, siniestro_id).get_json()

    assert body["resultado"] == "pendiente_revision"
    assert body["tratamiento"] == "revision_humana"
    assert body["reglasAplicadas"][0]["codigo"] == "FALLO_EVALUADOR"
    assert body["decisionAutomatica"]["elegible"] is True
    assert body["decisionAutomatica"]["requiereRevisionHumana"] is True
    assert body["estadoResultante"] == "en_evaluacion"


def test_sin_reglas_vigentes_queda_pendiente_de_revision(app: Flask, client: FlaskClient) -> None:
    publicar_reglas(client, vigenteDesde="2099-01-01T00:00:00Z")
    siniestro_id = registrar(app)

    body = evaluar(client, siniestro_id).get_json()

    assert body["resultado"] == "pendiente_revision"
    assert body["reglasVersion"] is None
    assert body["reglasAplicadas"][0]["codigo"] == "SIN_REGLAS_VIGENTES"
    assert body["decisionAutomatica"]["reglas"] == []


def test_usa_la_version_vigente_mas_reciente(app: Flask, client: FlaskClient) -> None:
    publicar_reglas(client)
    publicar_reglas(client, version=2, tratamientoPorDefecto="inspeccion")
    siniestro_id = registrar(app)

    body = evaluar(client, siniestro_id).get_json()

    assert body["reglasVersion"] == 2
    assert body["tratamiento"] == "inspeccion"


def test_registro_de_reglas_es_inmutable_por_version(client: FlaskClient) -> None:
    publicar_reglas(client)

    repetida = client.post(
        "/internal/v1/reglas-evaluacion-siniestros", json=REGLAS, headers=HEADERS
    )
    distinta = client.post(
        "/internal/v1/reglas-evaluacion-siniestros",
        json={**REGLAS, "tratamientoPorDefecto": "inspeccion"},
        headers=HEADERS,
    )
    vigencia = client.post(
        "/internal/v1/reglas-evaluacion-siniestros",
        json={**REGLAS, "version": 3, "vigenteHasta": "2025-01-01T00:00:00Z"},
        headers=HEADERS,
    )

    assert repetida.status_code == 200
    assert distinta.status_code == 409
    assert distinta.get_json()["code"] == "version_regla_conflictiva"
    assert vigencia.status_code == 422
    assert vigencia.get_json()["code"] == "vigencia_invalida"


def test_procesamiento_por_lote_evalua_reportados_pendientes(
    app: Flask, client: FlaskClient
) -> None:
    publicar_reglas(client, evidenciaMinima=2)
    completo = registrar(app, evidencias=2)
    incompleto = registrar(app, evidencias=1)
    registrar(app, tipo="parametrico", evidencias=2)
    registrar(app, estado="en_evaluacion", evidencias=2)

    primera = client.post(
        "/internal/v1/evaluaciones-siniestros/procesamiento", json={"limite": 10}, headers=HEADERS
    )
    segunda = client.post(
        "/internal/v1/evaluaciones-siniestros/procesamiento", json={"limite": 10}, headers=HEADERS
    )
    confirmar_evidencia(app, incompleto)
    tercera = client.post(
        "/internal/v1/evaluaciones-siniestros/procesamiento", json={"limite": 10}, headers=HEADERS
    )

    assert primera.get_json() == {
        "evaluados": 2,
        "decididos": 1,
        "informacionInsuficiente": 1,
        "pendientesRevision": 0,
    }
    assert segunda.get_json()["evaluados"] == 0
    assert tercera.get_json()["decididos"] == 1
    assert historial_estados(app, completo) == ["reportado", "en_evaluacion"]
    assert historial_estados(app, incompleto) == ["reportado", "en_evaluacion"]


def test_siniestros_no_evaluables(app: Flask, client: FlaskClient) -> None:
    publicar_reglas(client)
    parametrico = registrar(app, tipo="parametrico")
    avanzado = registrar(app, estado="en_evaluacion")

    assert evaluar(client, parametrico).get_json()["code"] == "siniestro_no_evaluable"
    assert evaluar(client, avanzado).status_code == 409
    assert evaluar(client, str(uuid4())).status_code == 404
    assert (
        client.get(f"/internal/v1/siniestros/{uuid4()}/evaluaciones", headers=HEADERS).status_code
        == 404
    )


def test_siniestro_no_integro_queda_pendiente_sin_cambiar_estado(
    app: Flask, client: FlaskClient
) -> None:
    publicar_reglas(client)
    siniestro_id = registrar(app)
    with Session(app.extensions["siniestros_engine"]) as session, session.begin():
        claim = session.get(Claim, siniestro_id)
        assert claim is not None
        claim.payload = {"polizaId": "no-es-uuid"}

    body = evaluar(client, siniestro_id).get_json()

    assert body["resultado"] == "pendiente_revision"
    assert body["reglasAplicadas"][0]["codigo"] == "SINIESTRO_NO_INTEGRO"
    assert body["estadoResultante"] == "reportado"
    assert historial_estados(app, siniestro_id) == ["reportado"]


def test_rutas_de_evaluacion_requieren_token_interno(client: FlaskClient) -> None:
    siniestro_id = UUID(int=1)
    rutas = [
        ("post", "/internal/v1/reglas-evaluacion-siniestros"),
        ("post", f"/internal/v1/siniestros/{siniestro_id}/evaluacion"),
        ("get", f"/internal/v1/siniestros/{siniestro_id}/evaluaciones"),
        ("post", "/internal/v1/evaluaciones-siniestros/procesamiento"),
    ]
    for metodo, ruta in rutas:
        response = getattr(client, metodo)(ruta, headers={"Authorization": "Bearer otro"})
        assert response.status_code == 401


def test_contrato_documenta_las_rutas_de_evaluacion() -> None:
    contrato = yaml.safe_load(CONTRATO.read_text(encoding="utf-8"))

    assert set(contrato["paths"]) == {
        "/reglas-evaluacion-siniestros",
        "/siniestros/{siniestroId}/evaluacion",
        "/siniestros/{siniestroId}/evaluaciones",
        "/evaluaciones-siniestros/procesamiento",
    }
    assert contrato["servers"] == [{"url": "/internal/v1"}]
    requeridos = contrato["components"]["schemas"]["DecisionAutomatica"]["required"]
    assert "requiereRevisionHumana" in requeridos
