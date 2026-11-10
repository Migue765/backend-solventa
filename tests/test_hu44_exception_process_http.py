import json
import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from socket import socket
from tempfile import TemporaryDirectory
from typing import Any
from uuid import UUID, uuid4

from jsonschema import Draft202012Validator
from perfilamiento.persistence import (
    ConsultaInformacionModel,
    EventoExcepcionOpenFinanceModel,
    ExcepcionOpenFinanceModel,
    OperacionInformacionModel,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from scripts.validate_openapi import load_contract
from tests.jmeter.local_stack import LocalStack
from tests.test_encrypted_local_stack import call, encrypted_login_payload


def _login(stack: LocalStack, email: str, password: str) -> dict[str, str]:
    status, challenge = call(stack.urls["web"] + "/v1/sesiones/desafio")
    assert status == 200
    status, tokens = call(
        stack.urls["web"] + "/v1/sesiones",
        encrypted_login_payload(challenge, email, password),
    )
    assert status == 200
    return {"Authorization": "Bearer " + tokens["accessToken"]}


def _customer(stack: LocalStack, password: str) -> tuple[str, dict[str, str]]:
    email = f"hu44-{uuid4().hex}@example.com"
    status, created = call(
        stack.urls["identity"] + "/v1/usuarios",
        {
            "nombre": "Cliente sintetico HU44",
            "correo": email,
            "contrasena": password,
            "tipoDocumento": "passport",
            "numeroDocumento": uuid4().hex[:16].upper(),
            "telefono": "+573001234567",
            "fechaNacimiento": "1990-01-01",
        },
    )
    assert status == 201
    auth = _login(stack, email, password)
    status, _ = call(
        stack.urls["web"] + "/v1/consentimientos/perfilamiento",
        {
            "autorizado": True,
            "version": 0,
            "solicitud": {
                "finalidad": "cotizacion",
                "alcances": ["open_finance", "open_data"],
                "fuentes": ["open_finance", "open_data"],
                "datos": ["indicador_riesgo"],
                "versionTerminos": "1.0",
                "vigenteHasta": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            },
        },
        auth,
        method="PUT",
    )
    assert status == 200
    return str(created["usuario"]["id"]), auth


def test_hu44_producer_decision_application_and_requester_over_http(
    tmp_path: Path, record_property: Callable[[str, object], None]
) -> None:
    evidence: dict[str, Any] = {"cases": [], "transport": "owned real HTTP with JWE/JWT"}
    contract = load_contract(
        Path(__file__).resolve().parents[1] / "contracts" / "openapi" / "bff-web-v1.yaml"
    )

    def validate(schema: str, payload: dict[str, Any]) -> None:
        Draft202012Validator(
            {
                "$ref": f"#/components/schemas/{schema}",
                "components": contract["components"],
            }
        ).validate(payload)

    with TemporaryDirectory(prefix="hu44-exception-http-", dir=tmp_path) as directory:
        with LocalStack(Path(directory), encrypted_login_enabled=True) as stack:
            ports = [server.server_port for server in stack.servers.values()]
            threads = list(stack.threads.values())
            record_property("owned_ports", json.dumps(ports))
            record_property("owned_directory", directory)
            assert not {4200, 4300}.intersection(ports)
            assert all(
                call(url + ("/health" if name in {"profile", "policies", "claims"} else "/ping"))[0]
                == 200
                for name, url in stack.urls.items()
            )
            assert (
                stack.apps["web"].extensions["client_information_gateway"]._base_url
                == stack.urls["profile"]
            )
            assert (
                stack.apps["web"].extensions["open_finance_exception_gateway"]._base_url
                == stack.urls["profile"]
            )
            password = "Local-Test-Password!"
            partner_id = "HU44-HTTP"
            actors: dict[str, dict[str, str]] = {}
            for role, command in (("socio", "create-partner-user"), ("asesor", "create-advisor")):
                email = f"{role}-{uuid4().hex}@example.com"
                created = (
                    stack.apps["identity"]
                    .test_cli_runner()
                    .invoke(
                        args=[
                            command,
                            "--name",
                            "Actor sintetico HU44",
                            "--email",
                            email,
                            "--password",
                            password,
                            "--partner-id",
                            partner_id,
                            "--partner-name",
                            "Socio sintetico HU44",
                        ]
                    )
                )
                assert created.exit_code == 0, created.output
                actors[role] = _login(stack, email, password)
            service = stack.apps["profile"].extensions["consultar_informacion"]
            finance = service._fuentes["open_finance"]
            repository = stack.apps["profile"].extensions["perfiles"]
            web = stack.urls["web"]
            for outcome in ("completada", "detenida", "bloqueada"):
                customer, _ = _customer(stack, password)
                operation = str(uuid4())
                finance._escenario = "no_disponible"
                status, blocked = call(
                    web + "/v1/socios/consultas-informacion-cliente",
                    {
                        "operationId": operation,
                        "socioId": partner_id,
                        "clienteId": customer,
                        "productoId": "mortgage-life",
                        "finalidad": "cotizacion",
                        "datosSolicitados": ["indicador_riesgo"],
                    },
                    {**actors["socio"], "Idempotency-Key": operation},
                )
                assert status == 503
                assert blocked["code"] == "consulta_bloqueada"
                validate("Problema", blocked)
                state = blocked["operacion"]
                assert state["operationId"] == operation and state["clienteId"] == customer
                exception_id = state["excepcionId"]
                case_url = web + "/v1/asesor/excepciones-open-finance/" + exception_id
                status, pending = call(
                    web + "/v1/asesor/excepciones-open-finance?socioId=" + partner_id,
                    headers=actors["asesor"],
                )
                assert status == 200
                assert any(case["excepcionId"] == exception_id for case in pending["excepciones"])
                pending_case = next(
                    case for case in pending["excepciones"] if case["excepcionId"] == exception_id
                )
                validate("ExcepcionOpenFinance", pending_case)
                assert all(
                    pending_case[field] is None
                    for field in ("decision", "actorId", "resueltaEn", "motivo")
                )
                with Session(repository.engine) as session:
                    saved = session.get(OperacionInformacionModel, (partner_id, UUID(operation)))
                    assert saved is not None and saved.contenido is not None
                    assert saved.contenido["aplicacion"]["estado"] == "esperando_decision"
                    assert (
                        session.get(ConsultaInformacionModel, (partner_id, UUID(operation))) is None
                    )
                finance._escenario = "disponible" if outcome == "completada" else "no_disponible"
                status, resolved = call(
                    case_url + "/resolver?socioId=" + partner_id,
                    {
                        "decision": "rechazar" if outcome == "detenida" else "aprobar",
                        "motivo": "Revision de la operacion sintetica bloqueada",
                    },
                    actors["asesor"],
                )
                assert status == 200
                validate("ExcepcionOpenFinanceResuelta", resolved)
                application = resolved["aplicacion"]
                assert application["estado"] == outcome
                assert application["acuseId"] is not None
                assert application["intentos"] == (0 if outcome == "detenida" else 1)
                status, listed = call(
                    web + f"/v1/asesor/excepciones-open-finance?socioId={partner_id}"
                    f"&estadoAplicacion={outcome}",
                    headers=actors["asesor"],
                )
                assert status == 200
                assert any(case["excepcionId"] == exception_id for case in listed["excepciones"])
                status, repeated = call(
                    case_url + "/aplicar?socioId=" + partner_id, {}, actors["asesor"]
                )
                assert status == 200 and repeated["aplicacion"] == application
                validate("ExcepcionOpenFinanceResuelta", repeated)
                state_url = (
                    web + f"/v1/socios/consultas-informacion-cliente/{operation}/estado"
                    f"?socioId={partner_id}"
                )
                status, received = call(state_url, headers=actors["socio"])
                assert status == 200
                assert received["operationId"] == operation
                assert received["aplicacion"] == application
                validate("EstadoOperacionInformacion", received)
                result_status, result = call(
                    web + f"/v1/socios/consultas-informacion-cliente/{operation}"
                    f"?socioId={partner_id}",
                    headers=actors["socio"],
                )
                assert result_status == (200 if outcome == "completada" else 404)
                if outcome == "completada":
                    assert result["estado"] == "completada" and result["operationId"] == operation
                with Session(repository.engine) as session:
                    row = session.get(OperacionInformacionModel, (partner_id, UUID(operation)))
                    assert row is not None and row.contenido is not None
                    assert row.contenido["aplicacion"] == application
                    ack = session.get(EventoExcepcionOpenFinanceModel, UUID(application["acuseId"]))
                    assert ack is not None and ack.contenido["estadoActual"] == outcome
                evidence["cases"].append(
                    {
                        "operationId": operation,
                        "clienteId": customer,
                        "excepcionId": exception_id,
                        "initial_status": 503,
                        "blocked_problem": blocked,
                        "pending_case": pending_case,
                        "applied_case": repeated,
                        "application": application,
                        "requester_http_status": status,
                        "requester_received": received,
                        "financial_result_status": result_status,
                        "ack_is_application_not_delivery_receipt": True,
                    }
                )
            with Session(repository.engine) as session:
                counts = {
                    "cases": session.scalar(
                        select(func.count()).select_from(ExcepcionOpenFinanceModel)
                    ),
                    "operations": session.scalar(
                        select(func.count()).select_from(OperacionInformacionModel)
                    ),
                    "successful_results": session.scalar(
                        select(func.count()).select_from(ConsultaInformacionModel)
                    ),
                    "events": session.scalar(
                        select(func.count()).select_from(EventoExcepcionOpenFinanceModel)
                    ),
                }
            assert counts == {"cases": 3, "operations": 3, "successful_results": 1, "events": 9}
            evidence["sql"] = counts
        assert all(not thread.is_alive() for thread in threads)
        for port in ports:
            with socket() as connection:
                connection.settimeout(0.5)
                assert connection.connect_ex(("127.0.0.1", port)) != 0
    assert not Path(directory).exists()
    evidence["cleanup"] = {
        "ports": ports,
        "closed": True,
        "threads_stopped": True,
        "directory_removed": True,
    }
    record_property("owned_ports_closed", json.dumps(ports))
    if path := os.environ.get("HU44_EXCEPTION_EVIDENCE"):
        Path(path).write_text(json.dumps(evidence, indent=2), encoding="utf-8")
