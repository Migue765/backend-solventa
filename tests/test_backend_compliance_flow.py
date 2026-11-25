import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import jwt
import pytest
import yaml
from bff_web.domain.auth import User
from bff_web.domain.operational_reports import (
    ClaimRecord,
    PaymentRecord,
    PolicyRecord,
    PremiumRecord,
    ReportSnapshot,
    ReportSourceUnavailableError,
    fingerprint,
)
from bff_web.infrastructure.http_compliance_gateway import HttpComplianceGateway
from bff_web.infrastructure.http_report_sources import HttpReportSources
from cryptography.hazmat.primitives.asymmetric import rsa
from openapi_schema_validator import OAS31Validator
from polizas.app import create_app as policies_app
from polizas.infrastructure.cumplimiento import RepositorioCumplimiento
from polizas.infrastructure.models import (
    AuditoriaCumplimientoModel,
    IntercambioReaseguroModel,
    ReporteRegulatorioModel,
)
from siniestros.app import create_app as claims_app
from siniestros.domain import Config as ClaimsConfig
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from tests import test_policy_renewal_flow as renewal
from tests.claims_local_stack import migrate, serve
from tests.test_dashboard_flow import (
    INTERNAL_TOKEN,
    QuoteConfig,
    create_paid_request,
    dashboard_runtime,
    policy_server,
    sign_token,
)

keypair = renewal.keypair
runtime = renewal.runtime
CUT_OFF = datetime(2026, 10, 20, 12, tzinfo=UTC)
QUERY = {"desde": "2026-10-01", "hasta": "2026-10-20", "corte": CUT_OFF.isoformat()}
REPORTS = "/v1/reportes/regulatorios"
EXCHANGES = "/v1/reaseguros/intercambios"
ADVISOR = User(uuid4(), "Asesor sintetico", "asesor@example.test", "asesor")
CONTRACT = yaml.safe_load(
    (
        Path(__file__).parents[1] / "contracts" / "openapi" / "cumplimiento-academico-v1.yaml"
    ).read_text(encoding="utf-8")
)


def validate(schema: str, payload: object) -> None:
    document = {"$ref": f"#/components/schemas/{schema}", "components": CONTRACT["components"]}
    OAS31Validator(document, format_checker=OAS31Validator.FORMAT_CHECKER).validate(payload)


def headers(keypair: rsa.RSAPrivateKey, key: str | None = None) -> dict[str, str]:
    claims = jwt.decode(
        sign_token(keypair, ADVISOR, scope="asesor"), options={"verify_signature": False}
    )
    claims["socios"] = []
    claims["permisos"] = ["reportes:operativos"]
    claims["sid"] = str(uuid4())
    token = jwt.encode(claims, keypair, algorithm="RS256", headers={"kid": "dashboard-test"})
    result = {"Authorization": "Bearer " + token}
    if key:
        result["Idempotency-Key"] = key
    return result


class Sources:
    def __init__(self) -> None:
        self.fail = False
        self.orphan = False

    def fetch(self, cut_off: datetime, correlation_id: str) -> ReportSnapshot:
        if self.fail:
            raise ReportSourceUnavailableError("siniestros")
        policy = PolicyRecord(
            id=UUID(int=1),
            tipo="life",
            pagoId=UUID(int=2),
            emitidaEn=CUT_OFF,
            fechaInicio=date(2026, 10, 20),
            fechaFin=date(2026, 11, 20),
            estado="vigente",
        )
        premium = PremiumRecord(
            pagoId=UUID(int=2),
            producto="life",
            monto="100.00",
            moneda="COP",
            confirmadaEn=CUT_OFF,
        )
        claim = ClaimRecord(
            id=UUID(int=3),
            polizaId=UUID(int=9 if self.orphan else 1),
            tipo="asistido",
            estado="pagado",
            reportadoEn=CUT_OFF,
        )
        payment = PaymentRecord(
            id=UUID(int=4),
            siniestroId=UUID(int=3),
            estado="confirmado",
            monto="50.00",
            moneda="COP",
            creadoEn=CUT_OFF,
        )
        return ReportSnapshot(cut_off, (policy,), (premium,), (claim,), (payment,), ())


def generate(runtime: renewal.Runtime, keypair: rsa.RSAPrivateKey, key: str) -> Any:
    return runtime.bff.post(
        REPORTS,
        query_string=QUERY,
        json={"aceptaSimulacion": True},
        headers=headers(keypair, key),
    )


def test_report_snapshot_replay_download_and_reinsurance_survive_restart(
    runtime: renewal.Runtime,
    keypair: rsa.RSAPrivateKey,
) -> None:
    runtime.bff.application.extensions["report_sources"] = Sources()
    key = str(uuid4())
    created = generate(runtime, keypair, key)
    assert created.status_code == 201, created.get_json()
    report = created.get_json()
    validate("Reporte", report)
    assert report["contenido"]["sinValidezRegulatoria"] is True
    assert report["contenido"]["indicadores"]["primas"]["totales"] == [
        {"moneda": "COP", "monto": "100.00"}
    ]
    assert report["contenido"]["trazabilidad"]["polizas"][0]["versionHash"] == fingerprint(
        report["contenido"]["trazabilidad"]["polizas"][0]["datos"]
    )
    replay = generate(runtime, keypair, key)
    assert replay.status_code == 200 and replay.get_json() == report
    url = REPORTS + "/" + report["reporteId"]
    runtime.bff.application.extensions["report_sources"].fail = True
    recovered = generate(runtime, keypair, key)
    assert recovered.status_code == 200 and recovered.get_json() == report
    downloaded = runtime.bff.get(url + "/descarga", headers=headers(keypair))
    assert downloaded.status_code == 200
    validate("Contenido", downloaded.get_json())
    assert hashlib.sha256(downloaded.data).hexdigest() == report["contenidoHash"]
    assert downloaded.headers["Cache-Control"] == "no-store"
    assert runtime.bff.get(url, headers=headers(keypair)).get_json() == report
    body = {"reporteId": report["reporteId"], "aceptaSimulacion": True}
    exchange_key = str(uuid4())
    exchange = runtime.bff.post(EXCHANGES, json=body, headers=headers(keypair, exchange_key))
    assert exchange.status_code == 201, exchange.get_json()
    result = exchange.get_json()
    validate("Intercambio", result)
    assert result["estado"] == "conciliado" and result["simulado"] is True
    assert [p["monto"] for p in result["partidas"]] == ["20.00", "10.00"]
    assert result["acuse"]["envioId"] == result["intercambioId"]
    retry = runtime.bff.post(EXCHANGES, json=body, headers=headers(keypair, exchange_key))
    assert retry.status_code == 200 and retry.get_json() == result
    assert (
        runtime.bff.post(EXCHANGES, json=body, headers=headers(keypair, str(uuid4()))).status_code
        == 409
    )
    assert (
        runtime.bff.post(
            EXCHANGES,
            json={**body, "escenario": "diferencias"},
            headers=headers(keypair, exchange_key),
        ).status_code
        == 409
    )
    engine = runtime.policies.application.extensions["polizas_engine"]
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(ReporteRegulatorioModel)) == 1
        assert session.scalar(select(func.count()).select_from(IntercambioReaseguroModel)) == 1
        assert session.scalar(select(func.count()).select_from(AuditoriaCumplimientoModel)) == 7
    restarted = policies_app(runtime.database_url, renewal.INTERNAL_TOKEN)
    try:
        response = restarted.test_client().get(
            "/internal/v1/cumplimiento/reportes/" + report["reporteId"],
            headers={
                "Authorization": "Bearer " + renewal.INTERNAL_TOKEN,
                "X-Actor-ID": str(ADVISOR.user_id),
                "X-Actor-Rol": "asesor",
            },
        )
        assert response.status_code == 200 and response.get_json() == report
    finally:
        restarted.extensions["polizas_engine"].dispose()


def test_inconsistencies_source_failure_and_unauthorized_requests_do_not_emit(
    runtime: renewal.Runtime,
    keypair: rsa.RSAPrivateKey,
) -> None:
    source = Sources()
    runtime.bff.application.extensions["report_sources"] = source
    source.orphan = True
    rejected = generate(runtime, keypair, str(uuid4()))
    assert rejected.status_code == 422
    assert rejected.get_json()["inconsistencias"]["siniestrosSinPoliza"] == 1
    source.fail = True
    assert generate(runtime, keypair, str(uuid4())).status_code == 503
    source.fail = False
    assert runtime.bff.post(REPORTS, query_string=QUERY, json={}).status_code == 401
    customer = sign_token(keypair, scope="cliente")
    assert (
        runtime.bff.post(
            REPORTS,
            query_string=QUERY,
            json={"aceptaSimulacion": True},
            headers={"Authorization": "Bearer " + customer, "Idempotency-Key": str(uuid4())},
        ).status_code
        == 403
    )
    for body in (
        {},
        {"aceptaSimulacion": False},
        {"aceptaSimulacion": 1},
        {"aceptaSimulacion": True, "actorId": str(uuid4())},
    ):
        assert (
            runtime.bff.post(
                REPORTS, query_string=QUERY, json=body, headers=headers(keypair, str(uuid4()))
            ).status_code
            == 400
        )
    for path in (REPORTS, EXCHANGES):
        oversized = runtime.bff.post(
            path,
            query_string=QUERY if path == REPORTS else {},
            data=b" " * 2049,
            content_type="application/json",
            headers=headers(keypair, str(uuid4())),
        )
        assert oversized.status_code == 413
        assert oversized.mimetype == "application/problem+json"
    engine = runtime.policies.application.extensions["polizas_engine"]
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(ReporteRegulatorioModel)) == 0


def test_concurrent_generation_and_difference_reconciliation(
    runtime: renewal.Runtime,
    keypair: rsa.RSAPrivateKey,
) -> None:
    runtime.bff.application.extensions["report_sources"] = Sources()
    key = str(uuid4())
    outgoing = headers(keypair, key)

    def submit(_: int) -> str:
        with runtime.bff.application.test_client() as client:
            response = client.post(
                REPORTS, query_string=QUERY, json={"aceptaSimulacion": True}, headers=outgoing
            )
            assert response.status_code in {200, 201}, response.get_json()
            return str(response.get_json()["reporteId"])

    with ThreadPoolExecutor(max_workers=4) as pool:
        report_ids = set(pool.map(submit, range(4)))
    assert len(report_ids) == 1
    result = runtime.bff.post(
        EXCHANGES,
        json={"reporteId": report_ids.pop(), "aceptaSimulacion": True, "escenario": "diferencias"},
        headers=headers(keypair, str(uuid4())),
    )
    assert result.status_code == 201
    validate("Intercambio", result.get_json())
    assert result.get_json()["estado"] == "revision_pendiente"
    assert result.get_json()["diferencias"][0]["recibido"] == "20.01"


def test_replay_conflicts_authorization_and_reinsurance_respect_report_filters(
    runtime: renewal.Runtime,
    keypair: rsa.RSAPrivateKey,
) -> None:
    source = Sources()
    runtime.bff.application.extensions["report_sources"] = source
    key = str(uuid4())
    query = {**QUERY, "hasta": "2026-10-19", "producto": "life"}
    response = runtime.bff.post(
        REPORTS, query_string=query, json={"aceptaSimulacion": True}, headers=headers(keypair, key)
    )
    assert response.status_code == 201, response.get_json()
    report = response.get_json()
    source.fail = True
    for changed in ({**query, "producto": "mortgage-life"}, {**query, "hasta": "2026-10-20"}):
        conflict = runtime.bff.post(
            REPORTS,
            query_string=changed,
            json={"aceptaSimulacion": True},
            headers=headers(keypair, key),
        )
        assert conflict.status_code == 409, conflict.get_json()
    body = {"reporteId": report["reporteId"], "aceptaSimulacion": True}
    empty_difference = runtime.bff.post(
        EXCHANGES,
        json={**body, "escenario": "diferencias"},
        headers=headers(keypair, str(uuid4())),
    )
    assert empty_difference.status_code == 422
    exchange = runtime.bff.post(EXCHANGES, json=body, headers=headers(keypair, str(uuid4())))
    assert exchange.status_code == 201, exchange.get_json()
    assert exchange.get_json()["partidas"] == []
    validate("Intercambio", exchange.get_json())
    url = REPORTS + "/" + report["reporteId"]
    exchange_url = EXCHANGES + "/" + exchange.get_json()["intercambioId"]
    customer = {"Authorization": "Bearer " + sign_token(keypair, scope="cliente")}
    for path in (url, url + "/descarga", exchange_url):
        assert runtime.bff.get(path).status_code == 401
        assert runtime.bff.get(path, headers=customer).status_code == 403
    assert runtime.bff.get(exchange_url, headers=headers(keypair)).get_json() == exchange.get_json()


def test_failed_audit_rolls_back_snapshot_and_same_key_can_retry(
    runtime: renewal.Runtime,
    keypair: rsa.RSAPrivateKey,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime.bff.application.extensions["report_sources"] = Sources()
    key = str(uuid4())

    def fail_audit(*_: object) -> None:
        raise SQLAlchemyError("Fallo sintetico de persistencia")

    with monkeypatch.context() as patch:
        patch.setattr(RepositorioCumplimiento, "_auditar", staticmethod(fail_audit))
        assert generate(runtime, keypair, key).status_code == 503
    engine = runtime.policies.application.extensions["polizas_engine"]
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(ReporteRegulatorioModel)) == 0
        assert session.scalar(select(func.count()).select_from(AuditoriaCumplimientoModel)) == 0
    assert generate(runtime, keypair, key).status_code == 201


def test_regulatory_report_uses_main_quote_payment_and_policy_http_sources(
    tmp_path: Path,
    keypair: rsa.RSAPrivateKey,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with policy_server(tmp_path) as (_, policies_url):
        config = QuoteConfig(
            database_url=f"sqlite:///{tmp_path / 'cotizacion.db'}",
            internal_api_token=INTERNAL_TOKEN,
            polizas_url=policies_url,
        )
        with dashboard_runtime(tmp_path, keypair, monkeypatch, config) as (bff, _, _):
            customer_token = sign_token(keypair)
            _, _, payment = create_paid_request(bff, customer_token, "life")
            claims_database = f"sqlite:///{tmp_path / 'siniestros.db'}"
            migrate("siniestros", claims_database)
            claims = claims_app(
                ClaimsConfig(
                    database_url=claims_database,
                    storage_root=tmp_path / "private",
                    internal_token=INTERNAL_TOKEN,
                    policies_url=policies_url,
                )
            )
            try:
                with serve(claims) as claims_url:
                    active = bff.application.extensions["bff_config"]
                    bff.application.extensions["report_sources"] = HttpReportSources(
                        policies_url, active.quote_service_url, claims_url, INTERNAL_TOKEN
                    )
                    bff.application.extensions["compliance_gateway"] = HttpComplianceGateway(
                        policies_url, INTERNAL_TOKEN
                    )
                    now = datetime.now(UTC)
                    response = bff.post(
                        REPORTS,
                        query_string={
                            "desde": now.date().isoformat(),
                            "hasta": now.date().isoformat(),
                            "corte": now.isoformat(),
                        },
                        json={"aceptaSimulacion": True},
                        headers=headers(keypair, str(uuid4())),
                    )
                    assert response.status_code == 201, response.get_json()
                    validate("Reporte", response.get_json())
                    report = response.get_json()["contenido"]
                    assert report["indicadores"]["polizas"]["cartera"] == 1
                    assert report["indicadores"]["primas"]["confirmadas"] == 1
                    assert {source["servicio"] for source in report["fuentes"]} == {
                        "polizas",
                        "primas",
                        "siniestros",
                    }
                    assert report["trazabilidad"]["primas"][0]["datos"]["pagoId"] == payment["id"]
                    assert "correo" not in json.dumps(report)
            finally:
                claims.extensions["siniestros_repository"].sessions.kw["bind"].dispose()
