from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import UUID, uuid4

import pytest
import yaml
from alembic import command
from alembic.config import Config as AlembicConfig
from flask import Flask
from siniestros.app import create_app
from siniestros.domain import Config
from siniestros.domain.fraude import ConfiguracionFraude
from siniestros.infrastructure.fraude import (
    FraudEvaluation,
    FraudReviewCase,
    MotorFraude,
)
from siniestros.infrastructure.models import CambioEstadoModel
from siniestros.models import Audit, Claim, Upload
from sqlalchemy import select
from sqlalchemy.orm import Session
from werkzeug.test import TestResponse

TOKEN = "siniestros-test-internal"
INTERNAL = {"Authorization": "Bearer " + TOKEN}
HOY = datetime.now(UTC)


def configured_app(tmp_path: Path, fraude: ConfiguracionFraude | None = None) -> Flask:
    url = f"sqlite:///{tmp_path / 'siniestros.db'}"
    migrations = AlembicConfig(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    migrations.set_main_option("sqlalchemy.url", url)
    command.upgrade(migrations, "head")
    return create_app(
        Config(url, tmp_path / "evidencias", TOKEN, "http://polizas.test"),
        fraude or ConfiguracionFraude(),
    )


@pytest.fixture
def poliza(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    vigente = {
        "id": str(uuid4()),
        "fechaInicio": (HOY - timedelta(days=365)).date().isoformat(),
        "fechaFin": (HOY + timedelta(days=365)).date().isoformat(),
        "estado": "vigente",
    }
    monkeypatch.setattr("siniestros.repository.policies", lambda *_: [vigente])
    return vigente


@pytest.fixture
def app(tmp_path: Path, poliza: dict[str, str]) -> Flask:
    return configured_app(tmp_path)


def regla(
    senal: str,
    umbral: str,
    peso: int,
    *,
    regla_id: UUID | None = None,
    version: int = 1,
    operador: str = "gte",
    vigente_hasta: str | None = None,
) -> dict[str, object]:
    return {
        "reglaId": str(regla_id or uuid4()),
        "version": version,
        "nombre": f"Regla {senal}",
        "motivo": f"La senal {senal} alcanza el umbral {umbral}.",
        "senal": senal,
        "operador": operador,
        "umbral": umbral,
        "peso": peso,
        "vigenteDesde": (HOY - timedelta(days=1)).isoformat(),
        "vigenteHasta": vigente_hasta,
    }


def registrar_regla(app: Flask, payload: dict[str, object]) -> TestResponse:
    return app.test_client().post("/internal/v1/reglas-fraude", headers=INTERNAL, json=payload)


def reportar(
    app: Flask,
    cliente: UUID,
    poliza: dict[str, str],
    evento: datetime | None = None,
) -> TestResponse:
    return app.test_client().post(
        "/v1/siniestros",
        headers={
            **INTERNAL,
            "X-Cliente-ID": str(cliente),
            "Idempotency-Key": str(uuid4()),
        },
        json={
            "polizaId": poliza["id"],
            "fechaEvento": (evento or HOY - timedelta(hours=2)).isoformat(),
            "tipoEvento": "Hurto",
            "descripcion": "Descripcion sintetica del evento reportado.",
            "lugar": "Bogota",
        },
    )


def deteccion(app: Flask, siniestro_id: str) -> dict[str, Any]:
    response = app.test_client().get(
        f"/internal/v1/siniestros/{siniestro_id}/deteccion-fraude", headers=INTERNAL
    )
    assert response.status_code == 200
    return dict(response.get_json())


def estado(app: Flask, cliente: UUID, siniestro_id: str) -> str:
    response = app.test_client().get(
        f"/v1/siniestros/{siniestro_id}/estado",
        headers={**INTERNAL, "X-Cliente-ID": str(cliente)},
    )
    assert response.status_code == 200
    return str(response.get_json()["estado"])


def pendientes(app: Flask) -> list[dict[str, Any]]:
    response = app.test_client().post(
        "/internal/v1/revision-fraude/pendientes", headers=INTERNAL, json={"limite": 50}
    )
    assert response.status_code == 200
    return list(response.get_json()["casos"])


def test_patron_conocido_se_enruta_a_revision_en_menos_de_un_segundo(
    app: Flask, poliza: dict[str, str]
) -> None:
    assert registrar_regla(app, regla("siniestros_cliente_90_dias", "2", 60)).status_code == 201
    cliente = uuid4()
    for _ in range(2):
        assert reportar(app, cliente, poliza).status_code == 201

    inicio = perf_counter()
    response = reportar(app, cliente, poliza)
    transcurrido = perf_counter() - inicio

    assert response.status_code == 201
    assert response.get_json()["estado"] == "registrado"
    siniestro_id = response.get_json()["id"]
    resultado = deteccion(app, siniestro_id)
    assert transcurrido <= 1
    assert resultado["resultado"] == "sospechoso"
    assert resultado["bloqueado"] is True
    assert resultado["accion"] == "revision_humana"
    assert resultado["dentroDePresupuesto"] is True
    assert resultado["duracionMs"] <= 1000
    assert resultado["origen"] == "registro"
    assert estado(app, cliente, siniestro_id) == "en_revision"
    with Session(app.extensions["siniestros_engine"]) as sesion:
        pasos = sesion.scalars(
            select(CambioEstadoModel.estado)
            .where(CambioEstadoModel.siniestro_id == siniestro_id)
            .order_by(CambioEstadoModel.secuencia)
        ).all()
    assert pasos == ["reportado", "en_evaluacion", "en_revision"]
    assert app.extensions["motor_fraude"].bloqueado(UUID(siniestro_id)) is True


def test_caso_legitimo_continua_segun_reglas_vigentes(app: Flask, poliza: dict[str, str]) -> None:
    assert registrar_regla(app, regla("siniestros_cliente_90_dias", "3", 60)).status_code == 201
    cliente = uuid4()

    response = reportar(app, cliente, poliza)

    siniestro_id = response.get_json()["id"]
    resultado = deteccion(app, siniestro_id)
    assert resultado["resultado"] == "legitimo"
    assert resultado["bloqueado"] is False
    assert resultado["prioridad"] is None
    assert resultado["evidencia"] == []
    assert resultado["reglas"][0]["activada"] is False
    assert estado(app, cliente, siniestro_id) == "reportado"
    assert pendientes(app) == []


def test_resultado_sospechoso_conserva_evidencia_reglas_versiones_y_motivo(
    app: Flask, poliza: dict[str, str]
) -> None:
    regla_id = uuid4()
    assert (
        registrar_regla(
            app, regla("horas_reporte_tras_evento", "1000", 50, regla_id=regla_id)
        ).status_code
        == 201
    )
    assert (
        registrar_regla(
            app, regla("horas_reporte_tras_evento", "48", 55, regla_id=regla_id, version=2)
        ).status_code
        == 201
    )
    assert (
        registrar_regla(
            app,
            regla(
                "siniestros_cliente_90_dias",
                "0",
                40,
                vigente_hasta=(HOY - timedelta(hours=1)).isoformat(),
            ),
        ).status_code
        == 201
    )

    response = reportar(app, uuid4(), poliza, HOY - timedelta(days=10))

    resultado = deteccion(app, response.get_json()["id"])
    assert resultado["resultado"] == "sospechoso"
    assert resultado["prioridad"] == "media"
    assert resultado["puntaje"] == 55
    assert resultado["versionMotor"] == "1.0.0"
    assert resultado["reglas"] == [
        {
            "reglaId": str(regla_id),
            "version": 2,
            "nombre": "Regla horas_reporte_tras_evento",
            "activada": True,
        }
    ]
    [evidencia] = resultado["evidencia"]
    assert evidencia["reglaId"] == str(regla_id)
    assert evidencia["version"] == 2
    assert evidencia["senal"] == "horas_reporte_tras_evento"
    assert Decimal(evidencia["valor"]) >= 240
    assert Decimal(evidencia["umbral"]) == 48
    assert "horas_reporte_tras_evento" in resultado["motivo"]
    assert "puntaje 55" in resultado["motivo"]
    assert Decimal(resultado["senales"]["horas_reporte_tras_evento"]) >= 240


def test_alerta_llega_a_revision_humana_priorizada_con_contexto(
    app: Flask, poliza: dict[str, str]
) -> None:
    assert registrar_regla(app, regla("horas_reporte_tras_evento", "48", 55)).status_code == 201
    assert registrar_regla(app, regla("horas_reporte_tras_evento", "200", 40)).status_code == 201
    media = reportar(app, uuid4(), poliza, HOY - timedelta(days=3)).get_json()["id"]
    alta = reportar(app, uuid4(), poliza, HOY - timedelta(days=20)).get_json()["id"]
    legitimo = reportar(app, uuid4(), poliza).get_json()["id"]

    casos = pendientes(app)

    assert [caso["siniestro"]["id"] for caso in casos] == [alta, media]
    assert legitimo not in {caso["siniestro"]["id"] for caso in casos}
    primero = casos[0]
    assert primero["prioridad"] == "alta"
    assert primero["puntaje"] == 95
    assert primero["estado"] == "pendiente"
    assert primero["siniestro"]["polizaId"] == poliza["id"]
    assert primero["siniestro"]["estado"] == "en_revision"
    assert primero["siniestro"]["tipoEvento"] == "Hurto"
    assert primero["siniestro"]["evidenciasConfirmadas"] == 0
    assert len(primero["evaluacion"]["evidencia"]) == 2
    assert casos[1]["prioridad"] == "media"


def test_motor_no_disponible_aplica_politica_y_audita_incidente(
    app: Flask, poliza: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def falla(*_: object) -> None:
        raise RuntimeError("motor caido")

    monkeypatch.setattr("siniestros.infrastructure.fraude.evaluar", falla)
    cliente = uuid4()

    response = reportar(app, cliente, poliza)

    assert response.status_code == 201
    siniestro_id = response.get_json()["id"]
    resultado = deteccion(app, siniestro_id)
    assert resultado["resultado"] == "indeterminado"
    assert resultado["politicaContinuidad"] == "revision_humana"
    assert resultado["incidente"] == "RuntimeError"
    assert resultado["bloqueado"] is True
    assert [caso["siniestro"]["id"] for caso in pendientes(app)] == [siniestro_id]
    assert estado(app, cliente, siniestro_id) == "en_revision"
    with Session(app.extensions["siniestros_engine"]) as session:
        incidente = session.scalar(
            select(Audit).where(Audit.action == "deteccion_fraude_incidente")
        )
        assert incidente is not None
        assert incidente.result == "motor_no_disponible"
        assert incidente.actor == str(cliente)
        assert incidente.correlation == resultado["correlationId"]


def test_politica_continuar_no_detiene_el_caso_y_audita_incidente(
    tmp_path: Path, poliza: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    app = configured_app(tmp_path, ConfiguracionFraude(politica_continuidad="continuar"))

    def falla(*_: object) -> None:
        raise RuntimeError("motor caido")

    monkeypatch.setattr("siniestros.infrastructure.fraude.evaluar", falla)
    cliente = uuid4()

    siniestro_id = reportar(app, cliente, poliza).get_json()["id"]

    resultado = deteccion(app, siniestro_id)
    assert resultado["resultado"] == "indeterminado"
    assert resultado["accion"] == "continuar"
    assert resultado["bloqueado"] is False
    assert estado(app, cliente, siniestro_id) == "reportado"
    assert pendientes(app) == []
    with Session(app.extensions["siniestros_engine"]) as session:
        assert session.scalar(select(Audit).where(Audit.action == "deteccion_fraude_incidente"))


def test_fallo_sin_persistencia_responde_503_y_no_interrumpe_el_flujo(
    app: Flask, poliza: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    siniestro_id = reportar(app, uuid4(), poliza).get_json()["id"]
    motor: MotorFraude = app.extensions["motor_fraude"]

    def falla(*_: object) -> None:
        raise RuntimeError("motor caido")

    def sin_persistencia(*_: object, **__: object) -> None:
        raise OSError("disco")

    monkeypatch.setattr("siniestros.infrastructure.fraude.evaluar", falla)
    monkeypatch.setattr("siniestros.infrastructure.fraude._enrutar", sin_persistencia)

    response = app.test_client().post(
        f"/internal/v1/siniestros/{siniestro_id}/deteccion-fraude",
        headers=INTERNAL,
        json={"senales": {"monto_reclamado_cop": 1}},
    )
    assert response.status_code == 503
    assert response.get_json()["code"] == "motor_fraude_no_disponible"
    motor.detectar_en_flujo(uuid4(), "registro", str(uuid4()))


def test_evidencia_duplicada_y_senales_externas(app: Flask, poliza: dict[str, str]) -> None:
    assert registrar_regla(app, regla("evidencias_duplicadas", "1", 50)).status_code == 201
    assert registrar_regla(app, regla("monto_reclamado_cop", "5000000", 30)).status_code == 201
    cliente = uuid4()
    primero = reportar(app, cliente, poliza).get_json()["id"]
    segundo = reportar(app, uuid4(), poliza).get_json()["id"]
    digest = "a" * 64
    with Session(app.extensions["siniestros_engine"]) as session, session.begin():
        for siniestro_id in (primero, segundo):
            session.add(
                Upload(
                    id=str(uuid4()),
                    claim_id=siniestro_id,
                    owner=str(cliente),
                    operation=str(uuid4()),
                    fingerprint="b" * 64,
                    mime="image/png",
                    size=10,
                    digest=digest,
                    location=None,
                    captured=None,
                    offset=10,
                    status="confirmada",
                    created=HOY.isoformat(),
                    confirmed=HOY.isoformat(),
                )
            )
    cliente_http = app.test_client()
    ruta = f"/internal/v1/siniestros/{segundo}/deteccion-fraude"

    reservada = cliente_http.post(
        ruta, headers=INTERNAL, json={"senales": {"evidencias_duplicadas": 0}}
    )
    response = cliente_http.post(
        ruta, headers=INTERNAL, json={"senales": {"monto_reclamado_cop": 9000000}}
    )
    repetida = cliente_http.post(
        ruta, headers=INTERNAL, json={"senales": {"monto_reclamado_cop": 9000000}}
    )

    assert reservada.status_code == 422
    assert reservada.get_json()["code"] == "senal_reservada"
    resultado = response.get_json()
    assert response.status_code == 200
    assert resultado["origen"] == "manual"
    assert resultado["puntaje"] == 80
    assert resultado["prioridad"] == "alta"
    assert resultado["senales"]["evidencias_duplicadas"] == "1"
    assert resultado["senales"]["evidencias_sin_ubicacion"] == "1"
    assert repetida.get_json()["evaluacionId"] == resultado["evaluacionId"]
    with Session(app.extensions["siniestros_engine"]) as session:
        caso = session.scalar(
            select(FraudReviewCase).where(FraudReviewCase.siniestro_id == segundo)
        )
        assert caso is not None and caso.prioridad == "alta"
        assert (
            len(
                session.scalars(
                    select(FraudEvaluation).where(FraudEvaluation.siniestro_id == segundo)
                ).all()
            )
            == 2
        )


def test_registro_de_reglas_versionado_e_idempotente(app: Flask) -> None:
    payload = regla("siniestros_cliente_90_dias", "2", 60)

    creada = registrar_regla(app, payload)
    repetida = registrar_regla(app, payload)
    conflictiva = registrar_regla(app, {**payload, "peso": 10})
    invalida = registrar_regla(
        app, {**payload, "version": 2, "vigenteHasta": (HOY - timedelta(days=2)).isoformat()}
    )
    sin_credencial = app.test_client().post("/internal/v1/reglas-fraude", json=payload)

    assert creada.status_code == 201
    assert creada.get_json()["version"] == 1
    assert repetida.status_code == 200
    assert repetida.get_json() == creada.get_json()
    assert conflictiva.status_code == 409
    assert conflictiva.get_json()["code"] == "version_regla_conflictiva"
    assert invalida.status_code == 422
    assert sin_credencial.status_code == 401


def test_contrato_interno_coincide_con_la_evaluacion(app: Flask, poliza: dict[str, str]) -> None:
    contrato = yaml.safe_load(
        (
            Path(__file__).resolve().parents[4]
            / "contracts"
            / "openapi"
            / "fraude-internal-v1.yaml"
        ).read_text(encoding="utf-8")
    )
    assert {
        "/reglas-fraude",
        "/siniestros/{siniestroId}/deteccion-fraude",
        "/revision-fraude/pendientes",
    } == set(contrato["paths"])
    esquemas = contrato["components"]["schemas"]
    assert registrar_regla(app, regla("horas_reporte_tras_evento", "0", 60)).status_code == 201
    reportar(app, uuid4(), poliza)

    [caso] = pendientes(app)

    assert set(caso) == set(esquemas["CasoRevision"]["required"])
    assert set(caso["siniestro"]) == set(
        esquemas["CasoRevision"]["properties"]["siniestro"]["required"]
    )
    assert set(caso["evaluacion"]) == set(esquemas["Evaluacion"]["required"])


def test_consultas_de_siniestro_inexistente_o_sin_evaluar(app: Flask) -> None:
    client = app.test_client()
    inexistente = uuid4()

    assert (
        client.get(
            f"/internal/v1/siniestros/{inexistente}/deteccion-fraude", headers=INTERNAL
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/internal/v1/siniestros/{inexistente}/deteccion-fraude",
            headers=INTERNAL,
            json={},
        ).status_code
        == 404
    )
    siniestro_id = str(uuid4())
    payload = {
        "polizaId": str(uuid4()),
        "fechaEvento": (HOY - timedelta(days=1)).isoformat(),
        "tipoEvento": "Hurto",
        "descripcion": "Descripcion sintetica del evento.",
        "lugar": "Bogota",
    }
    with Session(app.extensions["siniestros_engine"]) as session, session.begin():
        session.add(
            Claim(
                id=siniestro_id,
                owner=str(uuid4()),
                operation=str(uuid4()),
                fingerprint="c" * 64,
                payload=payload,
                created=HOY.isoformat(),
                tipo="asistido",
                estado="reportado",
                actualizado_en=HOY,
            )
        )
    sin_evaluar = client.get(
        f"/internal/v1/siniestros/{siniestro_id}/deteccion-fraude", headers=INTERNAL
    )
    assert sin_evaluar.status_code == 404
    assert sin_evaluar.get_json()["code"] == "evaluacion_no_encontrada"
