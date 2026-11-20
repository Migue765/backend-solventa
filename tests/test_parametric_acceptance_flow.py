"""Aceptacion local: fuente simulada -> workers CLI existentes -> pago academico."""

import json
import os
import secrets
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import pytest
from siniestros.domain import Config
from siniestros.models import (
    AutomaticPaymentCase,
    Claim,
    ParametricDecision,
    ParametricEvent,
    ParametricPayment,
    ParametricPaymentAttempt,
    ParametricPaymentEffect,
)
from sqlalchemy import func, select

from tests.claims_local_stack import ROOT, call, stack

SOURCE = "fuente-aceptacion-simulada"
FLOW_MODELS = (
    ParametricEvent,
    ParametricDecision,
    AutomaticPaymentCase,
    ParametricPayment,
    ParametricPaymentAttempt,
    ParametricPaymentEffect,
)


def run_worker(config: Config, worker: Literal["parametric_worker", "payment_worker"]) -> None:
    __tracebackhide__ = True
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "SYSTEMDRIVE"}
    }
    environment.update(
        {
            "PYTHONPATH": str(ROOT / "services" / "siniestros" / "src"),
            "SINIESTROS_DATABASE_URL": config.database_url,
            "SINIESTROS_STORAGE_ROOT": str(config.storage_root),
            "INTERNAL_API_TOKEN": config.internal_token,
            "POLIZAS_SERVICE_URL": config.policies_url,
            "FUENTES_PARAMETRICAS_JSON": json.dumps(config.parametric_sources),
        }
    )
    command = [sys.executable, "-m", f"siniestros.{worker}", "--once", "--limit", "1"]
    try:
        result = subprocess.run(
            command, cwd=ROOT, env=environment, capture_output=True, text=True, timeout=30
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"{worker} excedio el limite de 30 segundos.", pytrace=False)
    if result.returncode:
        diagnostic = result.stdout + result.stderr
        for credential in (config.internal_token, *config.parametric_sources.values()):
            diagnostic = diagnostic.replace(credential, "[REDACTADO]")
        pytest.fail(
            f"{worker} termino con codigo {result.returncode}:\n{diagnostic}", pytrace=False
        )


def claim_ids(running: dict[str, Any], user: dict[str, Any]) -> set[str]:
    status, body = call(running["web"] + "/siniestros", token=user["webToken"])
    assert status == 200, (status, body)
    return {item["id"] for item in body["siniestros"]}


def assert_flow_counts(running: dict[str, Any], expected: tuple[int, ...]) -> None:
    repository = running["app"].extensions["siniestros_repository"]
    with repository.sessions() as session:
        for model, count in zip(FLOW_MODELS, expected, strict=True):
            assert session.scalar(select(func.count()).select_from(model)) == count, (
                model.__tablename__
            )


def test_external_event_creates_and_pays_new_claim_once_through_cli(tmp_path: Path) -> None:
    source_token = secrets.token_urlsafe(32)
    with stack(tmp_path, parametric_sources={SOURCE: source_token}) as running:
        owner, other = running["users"]
        initial_owner = claim_ids(running, owner)
        initial_other = claim_ids(running, other)
        assert initial_owner == {owner["claim"]["id"]}
        assert initial_other == {other["claim"]["id"]}
        assert_flow_counts(running, (0, 0, 0, 0, 0, 0))

        occurred = datetime.now(UTC) - timedelta(seconds=1)
        status, policy = call(
            running["policies"] + "/v1/polizas",
            "POST",
            {
                "clienteId": owner["id"],
                "suscripcionId": str(uuid4()),
                "pagoId": str(uuid4()),
                "tipo": "parametric-weather",
                "confirmadaEn": occurred.isoformat(),
                "simulada": True,
                "fechasContratadas": {
                    "inicio": (occurred - timedelta(days=1)).date().isoformat(),
                    "fin": (occurred + timedelta(days=1)).date().isoformat(),
                },
            },
            running["internal"],
        )
        assert status == 200, (status, policy)
        assert policy["tipo"] == "parametric-weather"
        rule_id, beneficiary_id = str(uuid4()), str(uuid4())
        rule = {
            "reglaId": rule_id,
            "version": 1,
            "fuente": SOURCE,
            "tipoEvento": "lluvia_acumulada",
            "metrica": "precipitacion",
            "operador": "gte",
            "umbral": 50.0,
            "unidad": "mm",
            "ubicacionCodigo": "SINTETICA-001",
            "clienteId": owner["id"],
            "polizaId": policy["id"],
            "beneficiarioId": beneficiary_id,
            "montoIndemnizacion": "250000.00",
            "moneda": "COP",
            "vigenteDesde": (occurred - timedelta(days=1)).isoformat(),
            "vigenteHasta": (occurred + timedelta(days=1)).isoformat(),
        }
        status, registered = call(
            running["claims"] + "/internal/v1/reglas-parametricas",
            "POST",
            rule,
            running["internal"],
        )
        assert status == 201, (status, registered)
        event = {
            "eventoId": str(uuid4()),
            "ocurridoEn": occurred.isoformat(),
            "tipoEvento": "lluvia_acumulada",
            "ubicacionCodigo": "SINTETICA-001",
            "metrica": {"nombre": "precipitacion", "valor": 80.0, "unidad": "mm"},
        }
        reception = running["claims"] + "/internal/v1/eventos-parametricos/recepcion"
        source_headers = {"X-Fuente-ID": SOURCE}
        status, accepted = call(
            reception, "POST", {"eventos": [event]}, source_token, source_headers
        )
        assert status == 202, (status, accepted)
        assert accepted == {"aceptados": 1, "duplicados": 0, "pendientes": 1}
        assert claim_ids(running, owner) == initial_owner
        assert_flow_counts(running, (1, 0, 0, 0, 0, 0))

        run_worker(running["config"], "parametric_worker")
        new_ids = claim_ids(running, owner) - initial_owner
        assert len(new_ids) == 1
        claim_id = new_ids.pop()
        assert claim_ids(running, owner) == initial_owner | {claim_id}
        status_url = running["web"] + f"/clientes/me/siniestros/{claim_id}"
        status, approved = call(status_url, token=owner["webToken"])
        assert status == 200, (status, approved)
        assert (approved["id"], approved["polizaId"], approved["tipo"], approved["estado"]) == (
            claim_id,
            policy["id"],
            "parametrico",
            "aprobado",
        )
        assert [item["estado"] for item in approved["historial"]] == ["reportado", "aprobado"]
        assert_flow_counts(running, (1, 1, 1, 0, 0, 0))

        run_worker(running["config"], "payment_worker")
        status, paid = call(status_url, token=owner["webToken"])
        assert status == 200, (status, paid)
        assert (paid["id"], paid["tipo"], paid["estado"]) == (claim_id, "parametrico", "pagado")
        assert [item["estado"] for item in paid["historial"]] == [
            "reportado",
            "aprobado",
            "pagado",
        ]
        assert paid["historial"][:2] == approved["historial"]
        trace_url = running["claims"] + f"/internal/v1/pagos-parametricos/siniestros/{claim_id}"
        status, trace = call(trace_url, token=running["internal"])
        assert status == 200, (status, trace)
        assert trace["siniestroId"] == claim_id
        assert trace["evento"]["eventoId"] == event["eventoId"]
        assert trace["evento"]["fuente"] == SOURCE
        assert trace["regla"] == {"reglaId": rule_id, "version": 1}
        assert trace["beneficiarioId"] == beneficiary_id
        assert trace["indemnizacion"] == {"monto": "250000.00", "moneda": "COP"}
        assert trace["estado"] == "confirmado"
        assert [attempt["estado"] for attempt in trace["intentos"]] == ["confirmado"]
        assert_flow_counts(running, (1, 1, 1, 1, 1, 1))

        status, replay = call(reception, "POST", {"eventos": [event]}, source_token, source_headers)
        assert status == 202, (status, replay)
        assert replay == {"aceptados": 0, "duplicados": 1, "pendientes": 0}
        run_worker(running["config"], "parametric_worker")
        run_worker(running["config"], "payment_worker")
        assert claim_ids(running, owner) == initial_owner | {claim_id}
        assert call(status_url, token=owner["webToken"]) == (200, paid)
        assert call(trace_url, token=running["internal"]) == (200, trace)
        assert_flow_counts(running, (1, 1, 1, 1, 1, 1))

        repository = running["app"].extensions["siniestros_repository"]
        with repository.sessions() as session:
            stored_event = session.scalars(select(ParametricEvent)).one()
            decision = session.scalars(select(ParametricDecision)).one()
            case = session.scalars(select(AutomaticPaymentCase)).one()
            payment = session.scalars(select(ParametricPayment)).one()
            effect = session.scalars(select(ParametricPaymentEffect)).one()
            assert stored_event.estado == "procesado"
            assert decision.evento_id == case.evento_id == payment.evento_id == stored_event.id
            assert decision.decision == "valido"
            assert decision.regla_id == case.regla_id == payment.regla_id == rule_id
            assert decision.regla_version == case.regla_version == payment.regla_version == 1
            assert decision.siniestro_id == case.siniestro_id == payment.siniestro_id == claim_id
            assert case.id == payment.caso_id == trace["casoId"]
            assert payment.id == effect.pago_id == trace["pagoId"]
            assert effect.resultado == payment.estado == "confirmado"
            assert effect.huella == payment.huella
            assert set(session.scalars(select(Claim.id))) == initial_owner | initial_other | {
                claim_id
            }

        assert claim_ids(running, other) == initial_other
        status, forbidden = call(status_url, token=other["webToken"])
        assert status == 404, (status, forbidden)
        assert forbidden["code"] == "siniestro_no_encontrado"
