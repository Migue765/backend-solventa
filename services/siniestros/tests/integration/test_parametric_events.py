from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
import yaml
from alembic import command
from alembic.config import Config as AlembicConfig
from flask import Flask
from siniestros.app import create_app
from siniestros.domain import Config
from siniestros.models import (
    AutomaticPaymentCase,
    Claim,
    ParametricDecision,
    ParametricEvent,
    ParametricQueueState,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from werkzeug.test import TestResponse

CLIENT_HEADERS = {"Authorization": "Bearer siniestros-test-internal"}
INTERNAL_HEADERS = CLIENT_HEADERS
SOURCE = "ideam"
SOURCE_HEADERS = {"Authorization": "Bearer fuente-segura", "X-Fuente-ID": SOURCE}
OCCURRED = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    return configured_app(tmp_path)


def configured_app(tmp_path: Path, capacity: int = 1_100_000) -> Flask:
    url = f"sqlite:///{tmp_path / f'siniestros-{capacity}.db'}"
    migrations = AlembicConfig(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    migrations.set_main_option("sqlalchemy.url", url)
    command.upgrade(migrations, "head")
    return create_app(
        Config(
            url,
            tmp_path / f"evidencias-{capacity}",
            "siniestros-test-internal",
            "http://polizas.test",
            parametric_sources={SOURCE: "fuente-segura"},
            parametric_queue_capacity=capacity,
        )
    )


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "*" * 6}, SOURCE_HEADERS],
    ids=["ausente", "valor-fijo", "credencial-de-fuente"],
)
def test_operaciones_internas_exigen_credencial_configurada(
    app: Flask, headers: dict[str, str]
) -> None:
    route = "/internal/v1/eventos-parametricos/procesamiento"
    client = app.test_client()
    assert client.post(route, headers=headers, json={"limite": 1}).status_code == 401
    assert client.post(route, headers=CLIENT_HEADERS, json={"limite": 1}).status_code == 200


def rule(
    *,
    rule_id: UUID | None = None,
    version: int = 1,
    threshold: float = 50.0,
) -> dict[str, object]:
    return {
        "reglaId": str(rule_id or uuid4()),
        "version": version,
        "fuente": SOURCE,
        "tipoEvento": "lluvia_acumulada",
        "metrica": "precipitacion",
        "operador": "gte",
        "umbral": threshold,
        "unidad": "mm",
        "ubicacionCodigo": "CO-ANT-001",
        "clienteId": str(uuid4()),
        "polizaId": str(uuid4()),
        "vigenteDesde": "2026-01-01T00:00:00Z",
        "vigenteHasta": "2027-01-01T00:00:00Z",
    }


def event(event_id: UUID | None = None, value: float = 80.0) -> dict[str, object]:
    return {
        "eventoId": str(event_id or uuid4()),
        "ocurridoEn": OCCURRED.isoformat(),
        "tipoEvento": "lluvia_acumulada",
        "ubicacionCodigo": "CO-ANT-001",
        "metrica": {"nombre": "precipitacion", "valor": value, "unidad": "mm"},
    }


def register_rule(app: Flask, payload: dict[str, object]) -> None:
    response = app.test_client().post(
        "/internal/v1/reglas-parametricas",
        headers=INTERNAL_HEADERS,
        json=payload,
    )
    assert response.status_code == 201


def receive(app: Flask, *events: dict[str, object]) -> TestResponse:
    return app.test_client().post(
        "/internal/v1/eventos-parametricos/recepcion",
        headers=SOURCE_HEADERS,
        json={"eventos": list(events)},
    )


def process(app: Flask, limit: int = 5_000) -> TestResponse:
    return app.test_client().post(
        "/internal/v1/eventos-parametricos/procesamiento",
        headers=INTERNAL_HEADERS,
        json={"limite": limit},
    )


def test_procesa_evento_con_regla_vigente_y_entrega_caso_a_hu31(app: Flask) -> None:
    configured_rule = rule()
    register_rule(app, configured_rule)
    incoming = event()

    accepted = receive(app, incoming)
    processed = process(app)
    pending = app.test_client().post(
        "/internal/v1/casos-pago-automatico/pendientes",
        headers=INTERNAL_HEADERS,
        json={"limite": 10},
    )

    assert accepted.status_code == 202
    assert accepted.get_json() == {"aceptados": 1, "duplicados": 0, "pendientes": 1}
    assert processed.status_code == 200
    assert processed.get_json() == {"casosValidos": 1, "procesados": 1}
    cases = pending.get_json()["casos"]
    assert len(cases) == 1
    assert cases[0]["eventoId"] == incoming["eventoId"]
    assert cases[0]["fuente"] == SOURCE
    assert cases[0]["reglaId"] == configured_rule["reglaId"]
    assert cases[0]["reglaVersion"] == 1

    claim = app.test_client().get(
        f"/v1/siniestros/{cases[0]['siniestroId']}/estado",
        headers={
            **CLIENT_HEADERS,
            "X-Cliente-ID": str(configured_rule["clienteId"]),
        },
    )
    assert claim.status_code == 200
    assert claim.get_json()["tipo"] == "parametrico"
    assert claim.get_json()["estado"] == "aprobado"


def test_evento_repetido_se_acepta_una_vez_y_no_duplica_siniestro(app: Flask) -> None:
    register_rule(app, rule())
    incoming = event()

    assert receive(app, incoming, incoming).get_json() == {
        "aceptados": 1,
        "duplicados": 1,
        "pendientes": 1,
    }
    assert receive(app, incoming).get_json() == {
        "aceptados": 0,
        "duplicados": 1,
        "pendientes": 1,
    }
    assert process(app).get_json() == {"casosValidos": 1, "procesados": 1}
    assert process(app).get_json() == {"casosValidos": 0, "procesados": 0}

    with Session(app.extensions["siniestros_engine"]) as session:
        assert session.scalar(select(func.count()).select_from(ParametricEvent)) == 1
        assert session.scalar(select(func.count()).select_from(ParametricDecision)) == 1
        assert session.scalar(select(func.count()).select_from(Claim)) == 1
        assert session.scalar(select(func.count()).select_from(AutomaticPaymentCase)) == 1


def test_mismo_identificador_con_otro_contenido_es_conflicto(app: Flask) -> None:
    event_id = uuid4()
    assert receive(app, event(event_id, 80)).status_code == 202

    conflict = receive(app, event(event_id, 81))

    assert conflict.status_code == 409
    assert conflict.get_json()["code"] == "evento_conflictivo"


def test_reintentos_concurrentes_no_duplican_evento(app: Flask) -> None:
    incoming = event()

    def send(_: int) -> dict[str, int]:
        response = receive(app, incoming)
        assert response.status_code == 202
        return cast(dict[str, int], response.get_json())

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(send, range(8)))

    assert sum(result["aceptados"] for result in results) == 1
    assert sum(result["duplicados"] for result in results) == 7
    with Session(app.extensions["siniestros_engine"]) as session:
        assert session.scalar(select(func.count()).select_from(ParametricEvent)) == 1


def test_aplica_solo_la_version_mas_alta_vigente(app: Flask) -> None:
    rule_id = uuid4()
    first = rule(rule_id=rule_id, version=1, threshold=10)
    second = {
        **rule(rule_id=rule_id, version=2, threshold=100),
        "clienteId": first["clienteId"],
        "polizaId": first["polizaId"],
    }
    register_rule(app, first)
    register_rule(app, second)

    receive(app, event(value=80))
    assert process(app).get_json() == {"casosValidos": 0, "procesados": 1}

    with Session(app.extensions["siniestros_engine"]) as session:
        decision = session.scalar(select(ParametricDecision))
        assert decision is not None
        assert decision.regla_version == 2
        assert decision.decision == "no_valido"
        assert session.scalar(select(func.count()).select_from(Claim)) == 0


def test_backpressure_no_pierde_aceptados_y_libera_capacidad(tmp_path: Path) -> None:
    app = configured_app(tmp_path, capacity=1)
    first, second = event(), event()

    assert receive(app, first).status_code == 202
    rejected = receive(app, second)
    assert rejected.status_code == 429
    assert rejected.headers["Retry-After"] == "5"
    assert rejected.get_json()["code"] == "backpressure"

    with Session(app.extensions["siniestros_engine"]) as session:
        assert session.scalar(select(func.count()).select_from(ParametricEvent)) == 1
        state = session.get(ParametricQueueState, 1)
        assert state is not None and state.pendientes == 1

    assert process(app).get_json()["procesados"] == 1
    assert receive(app, second).status_code == 202


def test_fuente_debe_estar_identificada_y_autenticada(app: Flask) -> None:
    missing = app.test_client().post(
        "/internal/v1/eventos-parametricos/recepcion",
        json={"eventos": [event()]},
    )
    wrong = app.test_client().post(
        "/internal/v1/eventos-parametricos/recepcion",
        headers={"Authorization": "Bearer incorrecta", "X-Fuente-ID": SOURCE},
        json={"eventos": [event()]},
    )

    assert missing.status_code == wrong.status_code == 401
    assert missing.get_json()["code"] == wrong.get_json()["code"] == "fuente_no_autorizada"


def test_regla_y_evento_rechazan_campos_no_definidos(app: Flask) -> None:
    invalid_rule = {**rule(), "secreto": "no permitido"}
    invalid_event = {**event(), "clienteNombre": "Dato sensible"}

    rule_response = app.test_client().post(
        "/internal/v1/reglas-parametricas",
        headers=INTERNAL_HEADERS,
        json=invalid_rule,
    )
    event_response = receive(app, invalid_event)

    assert rule_response.status_code == event_response.status_code == 400
    assert rule_response.get_json()["code"] == "solicitud_invalida"
    assert event_response.get_json()["code"] == "solicitud_invalida"


def test_decision_conserva_fuente_evento_regla_version_fecha_y_resultado(app: Flask) -> None:
    configured_rule = rule(threshold=100)
    register_rule(app, configured_rule)
    incoming = event(value=80)
    receive(app, incoming)
    process(app)

    with Session(app.extensions["siniestros_engine"]) as session:
        stored_event = session.scalar(select(ParametricEvent))
        decision = session.scalar(select(ParametricDecision))
        assert stored_event is not None and decision is not None
        assert stored_event.fuente == SOURCE
        assert stored_event.evento_id == incoming["eventoId"]
        assert stored_event.ocurrido_en is not None
        assert decision.regla_id == configured_rule["reglaId"]
        assert decision.regla_version == configured_rule["version"]
        assert decision.decidida_en is not None
        assert decision.decision == "no_valido"


def test_confirmacion_hu31_es_idempotente_y_no_admite_otra_fecha(app: Flask) -> None:
    register_rule(app, rule())
    receive(app, event())
    process(app)
    pending = app.test_client().post(
        "/internal/v1/casos-pago-automatico/pendientes",
        headers=INTERNAL_HEADERS,
        json={"limite": 1},
    )
    case = pending.get_json()["casos"][0]
    delivered = datetime.fromisoformat(case["decididoEn"].replace("Z", "+00:00")).isoformat()
    route = f"/internal/v1/casos-pago-automatico/{case['casoId']}/confirmacion"

    first = app.test_client().put(route, headers=INTERNAL_HEADERS, json={"entregadoEn": delivered})
    replay = app.test_client().put(route, headers=INTERNAL_HEADERS, json={"entregadoEn": delivered})
    conflict = app.test_client().put(
        route,
        headers=INTERNAL_HEADERS,
        json={
            "entregadoEn": (datetime.fromisoformat(delivered) + timedelta(seconds=1)).isoformat()
        },
    )

    assert first.status_code == replay.status_code == 200
    assert first.get_json() == replay.get_json()
    assert conflict.status_code == 409
    assert conflict.get_json()["code"] == "confirmacion_conflictiva"
    assert app.test_client().post(
        "/internal/v1/casos-pago-automatico/pendientes",
        headers=INTERNAL_HEADERS,
        json={"limite": 1},
    ).get_json() == {"casos": []}


def test_evento_sin_regla_conserva_decision_explicita(app: Flask) -> None:
    receive(app, event())

    assert process(app).get_json() == {"casosValidos": 0, "procesados": 1}

    with Session(app.extensions["siniestros_engine"]) as session:
        decision = session.scalar(select(ParametricDecision))
        assert decision is not None
        assert decision.decision == "sin_regla_vigente"
        assert decision.regla_id is None
        assert decision.regla_version is None


def test_contrato_interno_publica_recepcion_backpressure_y_outbox() -> None:
    contract_path = (
        Path(__file__).resolve().parents[4]
        / "contracts"
        / "openapi"
        / "eventos-parametricos-internal-v1.yaml"
    )
    contract = yaml.safe_load(contract_path.read_text(encoding="utf-8"))

    reception = contract["paths"]["/eventos-parametricos/recepcion"]["post"]
    assert reception["responses"]["429"]
    assert (
        reception["requestBody"]["content"]["application/json"]["schema"]["properties"]["eventos"][
            "maxItems"
        ]
        == 5_000
    )
    assert "/casos-pago-automatico/pendientes" in contract["paths"]
    assert "/casos-pago-automatico/{casoId}/confirmacion" in contract["paths"]
