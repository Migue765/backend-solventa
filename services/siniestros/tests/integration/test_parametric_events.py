from collections.abc import Callable
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
from siniestros.domain import Config, Problem
from siniestros.domain.pagos_parametricos import (
    CasoPagoParametrico,
    RespuestaPagoAmbigua,
    ResultadoProveedorPago,
)
from siniestros.infrastructure.models import CambioEstadoModel
from siniestros.infrastructure.pagos_parametricos import (
    ProcesadorPagosParametricos,
    SimuladorPagoParametrico,
)
from siniestros.models import (
    AutomaticPaymentCase,
    Claim,
    ParametricDecision,
    ParametricEvent,
    ParametricPayment,
    ParametricPaymentAttempt,
    ParametricPaymentEffect,
    ParametricQueueState,
    ParametricRule,
)
from sqlalchemy import func, select, update
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
        "beneficiarioId": str(uuid4()),
        "montoIndemnizacion": "250000.00",
        "moneda": "COP",
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


def prepare_payment_case(app: Flask, configured_rule: dict[str, object] | None = None) -> dict:
    selected_rule = configured_rule or rule()
    register_rule(app, selected_rule)
    receive(app, event())
    assert process(app).get_json() == {"casosValidos": 1, "procesados": 1}
    with Session(app.extensions["siniestros_engine"]) as session:
        case = session.scalar(select(AutomaticPaymentCase))
        assert case is not None
        return dict(case.payload)


def process_payments(app: Flask, limit: int = 10) -> TestResponse:
    return app.test_client().post(
        "/internal/v1/pagos-parametricos/procesamiento",
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
    assert cases[0]["beneficiarioId"] == configured_rule["beneficiarioId"]
    assert cases[0]["indemnizacion"] == {"monto": "250000.00", "moneda": "COP"}

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


def test_backpressure_concurrente_de_varios_productores_no_excede_capacidad(
    tmp_path: Path,
) -> None:
    capacity = 10
    app = configured_app(tmp_path, capacity=capacity)
    batches = [[event() for _ in range(3)] for _ in range(8)]

    def send(batch: list[dict[str, object]]) -> tuple[int, dict[str, object]]:
        response = receive(app, *batch)
        return response.status_code, cast(dict[str, object], response.get_json())

    with ThreadPoolExecutor(max_workers=len(batches)) as executor:
        results = list(executor.map(send, batches))

    accepted = [batch for batch, (status, _) in zip(batches, results, strict=True) if status == 202]
    rejected = [batch for batch, (status, _) in zip(batches, results, strict=True) if status == 429]
    assert len(accepted) + len(rejected) == len(batches)
    assert 1 <= len(accepted) <= capacity // 3
    for status, body in results:
        if status == 202:
            assert body["aceptados"] == 3 and body["duplicados"] == 0
        else:
            assert body["code"] == "backpressure"

    accepted_ids = {item["eventoId"] for batch in accepted for item in batch}
    with Session(app.extensions["siniestros_engine"]) as session:
        stored_ids = set(session.scalars(select(ParametricEvent.evento_id)))
        state = session.get(ParametricQueueState, 1)
        assert state is not None
        assert stored_ids == accepted_ids
        assert state.pendientes == len(accepted_ids) <= capacity

    for _ in range(len(batches)):
        if not rejected:
            break
        assert process(app).status_code == 200
        rejected = [batch for batch in rejected if receive(app, *batch).status_code == 429]
    assert not rejected
    assert process(app).status_code == 200

    with Session(app.extensions["siniestros_engine"]) as session:
        assert session.scalar(select(func.count()).select_from(ParametricEvent)) == 24
        pending = session.scalar(
            select(func.count())
            .select_from(ParametricEvent)
            .where(ParametricEvent.estado == "pendiente")
        )
        state = session.get(ParametricQueueState, 1)
        assert pending == 0
        assert state is not None and state.pendientes == 0


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


def test_hu31_paga_caso_validado_y_actualiza_siniestro(app: Flask) -> None:
    case = prepare_payment_case(app)

    response = process_payments(app)
    detail = app.test_client().get(
        f"/internal/v1/pagos-parametricos/casos/{case['casoId']}",
        headers=INTERNAL_HEADERS,
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "casosIncorporados": 1,
        "confirmados": 1,
        "rechazados": 0,
        "pendientes": 0,
    }
    assert detail.status_code == 200
    payment = detail.get_json()
    assert payment["casoId"] == case["casoId"]
    assert payment["siniestroId"] == case["siniestroId"]
    assert payment["evento"]["eventoId"] == case["eventoId"]
    assert payment["regla"] == {
        "reglaId": case["reglaId"],
        "version": case["reglaVersion"],
    }
    assert payment["estado"] == "confirmado"
    assert payment["intentos"][0]["estado"] == "confirmado"

    claim = app.test_client().get(
        f"/v1/siniestros/{case['siniestroId']}/estado",
        headers={**CLIENT_HEADERS, "X-Cliente-ID": case["clienteId"]},
    )
    assert claim.status_code == 200
    assert claim.get_json()["estado"] == "pagado"


def test_hu31_redelivery_no_duplica_pago_efecto_intento_ni_indemnizacion(app: Flask) -> None:
    case = prepare_payment_case(app)
    assert process_payments(app).get_json()["confirmados"] == 1

    for _ in range(3):
        assert process_payments(app).get_json() == {
            "casosIncorporados": 0,
            "confirmados": 0,
            "rechazados": 0,
            "pendientes": 0,
        }

    with Session(app.extensions["siniestros_engine"]) as session:
        assert session.scalar(select(func.count()).select_from(ParametricPayment)) == 1
        assert session.scalar(select(func.count()).select_from(ParametricPaymentEffect)) == 1
        assert session.scalar(select(func.count()).select_from(ParametricPaymentAttempt)) == 1
        claim = session.get(Claim, case["siniestroId"])
        assert claim is not None and claim.estado == "pagado"


def test_hu31_no_ordena_pago_de_caso_no_validado(app: Flask) -> None:
    configured_rule = rule(threshold=100)
    register_rule(app, configured_rule)
    receive(app, event(value=80))
    assert process(app).get_json() == {"casosValidos": 0, "procesados": 1}

    assert process_payments(app).get_json() == {
        "casosIncorporados": 0,
        "confirmados": 0,
        "rechazados": 0,
        "pendientes": 0,
    }
    with Session(app.extensions["siniestros_engine"]) as session:
        assert session.scalar(select(func.count()).select_from(ParametricPayment)) == 0
        assert session.scalar(select(func.count()).select_from(ParametricPaymentEffect)) == 0


def test_hu31_rechaza_regla_sin_beneficiario_o_monto_valido(app: Flask) -> None:
    missing_beneficiary = rule()
    missing_beneficiary.pop("beneficiarioId")
    invalid_amount = {**rule(), "montoIndemnizacion": "0.00"}

    client = app.test_client()
    first = client.post(
        "/internal/v1/reglas-parametricas",
        headers=INTERNAL_HEADERS,
        json=missing_beneficiary,
    )
    second = client.post(
        "/internal/v1/reglas-parametricas",
        headers=INTERNAL_HEADERS,
        json=invalid_amount,
    )

    assert first.status_code == second.status_code == 400
    assert first.get_json()["code"] == second.get_json()["code"] == "solicitud_invalida"


def test_hu31_diagnostica_caso_historico_sin_datos_y_no_lo_reintenta(app: Flask) -> None:
    case = prepare_payment_case(app)
    with Session(app.extensions["siniestros_engine"]) as session, session.begin():
        stored = session.get(AutomaticPaymentCase, case["casoId"])
        assert stored is not None
        stored.payload = {
            key: value
            for key, value in stored.payload.items()
            if key not in {"beneficiarioId", "indemnizacion"}
        }

    first = process_payments(app)
    replay = process_payments(app)

    assert first.get_json()["casosIncorporados"] == 0
    assert replay.get_json()["casosIncorporados"] == 0
    with Session(app.extensions["siniestros_engine"]) as session:
        stored = session.get(AutomaticPaymentCase, case["casoId"])
        assert stored is not None
        assert stored.rechazado_en is not None
        assert stored.ultimo_error == "datos_financieros_invalidos"
        assert session.scalar(select(func.count()).select_from(ParametricPayment)) == 0
        assert session.scalar(select(func.count()).select_from(ParametricPaymentEffect)) == 0


def test_hu31_concilia_ack_perdido_sin_repetir_efecto(app: Flask) -> None:
    case = prepare_payment_case(app)
    repository = app.extensions["siniestros_repository"]
    simulated = SimuladorPagoParametrico(repository)
    clock = [datetime.now(UTC)]

    class LostAcknowledgement:
        def __init__(self) -> None:
            self.executions = 0

        def consultar(self, pago_id: UUID, huella: str) -> ResultadoProveedorPago | None:
            return simulated.consultar(pago_id, huella)

        def ejecutar(
            self,
            pago_id: UUID,
            payment_case: CasoPagoParametrico,
            huella: str,
        ) -> ResultadoProveedorPago:
            self.executions += 1
            simulated.ejecutar(pago_id, payment_case, huella)
            raise RespuestaPagoAmbigua("ack_perdido")

    provider = LostAcknowledgement()
    processor = ProcesadorPagosParametricos(repository, provider, lambda: clock[0])

    assert processor.procesar(10) == {
        "casosIncorporados": 1,
        "confirmados": 0,
        "rechazados": 0,
        "pendientes": 1,
    }
    clock[0] += timedelta(seconds=3)
    assert processor.procesar(10) == {
        "casosIncorporados": 0,
        "confirmados": 1,
        "rechazados": 0,
        "pendientes": 0,
    }
    assert provider.executions == 1

    detail = processor.detalle(UUID(case["casoId"]))
    assert detail["estado"] == "confirmado"
    attempts = cast(list[dict[str, object]], detail["intentos"])
    assert [attempt["estado"] for attempt in attempts] == [
        "ambiguo",
        "confirmado",
    ]
    with Session(app.extensions["siniestros_engine"]) as session:
        assert session.scalar(select(func.count()).select_from(ParametricPaymentEffect)) == 1


def test_hu31_conserva_rechazo_definitivo_sin_marcar_siniestro_pagado(app: Flask) -> None:
    case = prepare_payment_case(app)
    repository = app.extensions["siniestros_repository"]
    provider = SimuladorPagoParametrico(repository, ResultadoProveedorPago.RECHAZADO)
    processor = ProcesadorPagosParametricos(repository, provider)

    assert processor.procesar(10)["rechazados"] == 1
    detail = processor.detalle(UUID(case["casoId"]))
    assert detail["estado"] == "rechazado"
    attempts = cast(list[dict[str, object]], detail["intentos"])
    assert attempts[0]["estado"] == "rechazado"
    with Session(app.extensions["siniestros_engine"]) as session:
        claim = session.get(Claim, case["siniestroId"])
        assert claim is not None and claim.estado == "aprobado"


def test_hu31_failover_cierra_intento_huerfano_y_no_repite_efecto(app: Flask) -> None:
    case = prepare_payment_case(app)
    repository = app.extensions["siniestros_repository"]
    clock = [datetime.now(UTC)]
    crashed = ProcesadorPagosParametricos(repository, reloj=lambda: clock[0])
    replica = ProcesadorPagosParametricos(repository, reloj=lambda: clock[0])

    assert crashed._incorporar(10) == 1
    reservation = crashed._reservar()
    assert reservation is not None
    payment_id, _, _, stale_token, stale_number = reservation

    assert replica.procesar(10)["confirmados"] == 0
    clock[0] += timedelta(seconds=31)
    assert replica.procesar(10) == {
        "casosIncorporados": 0,
        "confirmados": 1,
        "rechazados": 0,
        "pendientes": 0,
    }

    with pytest.raises(Problem) as conflict:
        crashed._finalizar(payment_id, stale_token, stale_number, ResultadoProveedorPago.CONFIRMADO)
    assert conflict.value.status == 409
    assert conflict.value.code == "reserva_pago_conflictiva"

    detail = replica.detalle(UUID(case["casoId"]))
    attempts = cast(list[dict[str, object]], detail["intentos"])
    assert [(item["numero"], item["estado"], item["codigo"]) for item in attempts] == [
        (1, "ambiguo", "lease_vencido"),
        (2, "confirmado", None),
    ]
    assert all(item["finalizadoEn"] is not None for item in attempts)
    with Session(app.extensions["siniestros_engine"]) as session:
        assert session.scalar(select(func.count()).select_from(ParametricPaymentEffect)) == 1
        orphans = session.scalar(
            select(func.count())
            .select_from(ParametricPaymentAttempt)
            .where(ParametricPaymentAttempt.estado == "en_proceso")
        )
        assert orphans == 0


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("beneficiarioId", lambda: str(uuid4()), "beneficiario_invalido"),
        ("indemnizacion", lambda: {"monto": "999999.00", "moneda": "COP"}, "monto_invalido"),
        ("indemnizacion", lambda: {"monto": "250000.00", "moneda": "USD"}, "monto_invalido"),
        ("polizaId", lambda: str(uuid4()), "poliza_invalida"),
        ("eventoId", lambda: str(uuid4()), "evento_no_asociado"),
        ("reglaVersion", lambda: 2, "origen_no_validado"),
    ],
    ids=["beneficiario", "monto", "moneda", "poliza", "evento", "version"],
)
def test_hu31_valida_caso_contra_version_de_regla_persistida(
    app: Flask,
    field: str,
    value: Callable[[], object],
    code: str,
) -> None:
    case = prepare_payment_case(app)
    with Session(app.extensions["siniestros_engine"]) as session, session.begin():
        stored = session.get(AutomaticPaymentCase, case["casoId"])
        assert stored is not None
        stored.payload = {**stored.payload, field: value()}

    assert process_payments(app).get_json()["casosIncorporados"] == 0
    assert process_payments(app).get_json()["casosIncorporados"] == 0
    with Session(app.extensions["siniestros_engine"]) as session:
        stored = session.get(AutomaticPaymentCase, case["casoId"])
        assert stored is not None
        assert stored.rechazado_en is not None
        assert stored.ultimo_error == code
        assert session.scalar(select(func.count()).select_from(ParametricPayment)) == 0
        assert session.scalar(select(func.count()).select_from(ParametricPaymentEffect)) == 0
        claim = session.get(Claim, case["siniestroId"])
        assert claim is not None and claim.estado == "aprobado"


def test_hu31_rechaza_caso_si_la_version_persistida_no_respalda_el_monto(app: Flask) -> None:
    case = prepare_payment_case(app)
    with Session(app.extensions["siniestros_engine"]) as session, session.begin():
        stored_rule = session.scalar(
            select(ParametricRule).where(ParametricRule.regla_id == case["reglaId"])
        )
        assert stored_rule is not None
        stored_rule.monto_indemnizacion = None

    assert process_payments(app).get_json()["casosIncorporados"] == 0
    with Session(app.extensions["siniestros_engine"]) as session:
        stored = session.get(AutomaticPaymentCase, case["casoId"])
        assert stored is not None and stored.ultimo_error == "monto_invalido"
        assert session.scalar(select(func.count()).select_from(ParametricPayment)) == 0


def test_hu31_consulta_por_siniestro_reconstruye_evento_regla_e_intentos(app: Flask) -> None:
    case = prepare_payment_case(app)
    assert process_payments(app).get_json()["confirmados"] == 1
    client = app.test_client()

    by_case = client.get(
        f"/internal/v1/pagos-parametricos/casos/{case['casoId']}",
        headers=INTERNAL_HEADERS,
    )
    by_claim = client.get(
        f"/internal/v1/pagos-parametricos/siniestros/{case['siniestroId']}",
        headers=INTERNAL_HEADERS,
    )
    missing = client.get(
        f"/internal/v1/pagos-parametricos/siniestros/{uuid4()}",
        headers=INTERNAL_HEADERS,
    )
    unauthenticated = client.get(f"/internal/v1/pagos-parametricos/siniestros/{uuid4()}")

    assert by_claim.status_code == by_case.status_code == 200
    assert by_claim.get_json() == by_case.get_json()
    assert missing.status_code == 404
    assert missing.get_json()["code"] == "pago_parametrico_no_encontrado"
    assert unauthenticated.status_code == 401
    payment = by_claim.get_json()
    assert payment["evento"] == {
        "fuente": SOURCE,
        "eventoId": case["eventoId"],
        "ocurridoEn": OCCURRED.isoformat().replace("+00:00", "Z"),
        "recibidoEn": payment["evento"]["recibidoEn"],
    }
    assert payment["beneficiarioId"] == case["beneficiarioId"]
    assert payment["indemnizacion"] == case["indemnizacion"]

    schemas = load_internal_contract()["components"]["schemas"]["PagoParametrico"]
    assert set(payment) <= set(schemas["properties"])
    assert set(schemas["required"]) <= set(payment)
    for field in ("evento", "regla", "indemnizacion"):
        nested = schemas["properties"][field]
        assert nested["additionalProperties"] is False
        assert set(payment[field]) == set(nested["required"]) == set(nested["properties"])


def load_internal_contract() -> dict:
    contract_path = (
        Path(__file__).resolve().parents[4]
        / "contracts"
        / "openapi"
        / "eventos-parametricos-internal-v1.yaml"
    )
    return cast(dict, yaml.safe_load(contract_path.read_text(encoding="utf-8")))


def test_contrato_interno_publica_recepcion_backpressure_y_outbox() -> None:
    contract = load_internal_contract()

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
    assert "/pagos-parametricos/procesamiento" in contract["paths"]
    assert "/pagos-parametricos/conciliacion" in contract["paths"]
    assert "/pagos-parametricos/casos/{casoId}" in contract["paths"]
    assert "/pagos-parametricos/siniestros/{siniestroId}" in contract["paths"]
    required_rule_fields = contract["components"]["schemas"]["Regla"]["required"]
    assert {"beneficiarioId", "montoIndemnizacion", "moneda"} <= set(required_rule_fields)


def test_reporte_de_pagos_parametricos_al_corte_sin_beneficiario(app: Flask) -> None:
    case = prepare_payment_case(app)
    assert process_payments(app).status_code == 200
    creado = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
    with Session(app.extensions["siniestros_engine"]) as session, session.begin():
        session.execute(
            update(ParametricPayment).values(
                creado_en=creado, finalizado_en=creado + timedelta(hours=2)
            )
        )
        session.execute(update(Claim).values(created=(creado - timedelta(hours=1)).isoformat()))
        session.execute(update(CambioEstadoModel).values(ocurrido_en=creado - timedelta(hours=1)))
        session.execute(
            update(CambioEstadoModel)
            .where(CambioEstadoModel.estado == "pagado")
            .values(ocurrido_en=creado + timedelta(hours=2))
        )
        payment = session.scalars(select(ParametricPayment)).one()
        payment_id, beneficiary = payment.id, payment.beneficiario_id

    def report(corte: datetime) -> dict:
        response = app.test_client().post(
            "/internal/v1/reportes/consulta",
            headers=INTERNAL_HEADERS,
            json={"corte": corte.isoformat()},
        )
        assert response.status_code == 200
        return cast(dict, response.get_json())

    pending = report(creado + timedelta(hours=1))
    confirmed = report(creado + timedelta(hours=3))

    assert pending["pagos"] == [
        {
            "id": payment_id,
            "siniestroId": case["siniestroId"],
            "estado": "pendiente",
            "monto": "250000.00",
            "moneda": "COP",
            "creadoEn": "2026-11-08T12:00:00Z",
        }
    ]
    assert pending["siniestros"][0]["tipo"] == "parametrico"
    assert pending["siniestros"][0]["estado"] == "aprobado"
    assert pending["registros"] == 2
    assert confirmed["pagos"][0]["estado"] == "confirmado"
    assert confirmed["siniestros"][0]["estado"] == "pagado"
    assert beneficiary not in str(confirmed)
    assert case["clienteId"] not in str(confirmed)
    assert report(creado - timedelta(minutes=1))["pagos"] == []
