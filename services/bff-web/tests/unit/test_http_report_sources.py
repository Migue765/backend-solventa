from collections.abc import Callable
from typing import Any

import pytest
from bff_web.domain.operational_reports import ReportSourceUnavailableError
from bff_web.infrastructure.http_report_sources import HttpReportSources
from report_support import (
    CUT_OFF,
    CUT_OFF_TEXT,
    claims,
    claims_payload,
    payments,
    policies,
    policies_payload,
    premiums,
    premiums_payload,
)
from support import IdentityServer

POLICIES_PATH = "/v1/reportes/polizas?corte=2026-10-14T11%3A00%3A00Z"
PREMIUMS_PATH = "/v1/reportes/primas?corte=2026-10-14T11%3A00%3A00Z"
CLAIMS_PATH = "/internal/v1/reportes/consulta"
TOKEN = "token-interno"


def serve(
    server: IdentityServer,
    policies_body: object | None = None,
    premiums_body: object | None = None,
    claims_body: object | None = None,
) -> None:
    server.respond("GET", POLICIES_PATH, 200, policies_body or policies_payload())
    server.respond("GET", PREMIUMS_PATH, 200, premiums_body or premiums_payload())
    server.respond("POST", CLAIMS_PATH, 200, claims_body or claims_payload())


def sources(server: IdentityServer, **overrides: str) -> HttpReportSources:
    urls = {"policies": server.url, "premiums": server.url, "claims": server.url}
    urls.update(overrides)
    return HttpReportSources(urls["policies"], urls["premiums"], urls["claims"], TOKEN)


def test_consolida_las_tres_fuentes_al_mismo_corte(identity_server: IdentityServer) -> None:
    serve(identity_server)

    snapshot = sources(identity_server).fetch(CUT_OFF, "corr-1")

    assert snapshot.cut_off == CUT_OFF
    assert len(snapshot.policies) == 3
    assert len(snapshot.premiums) == 3
    assert len(snapshot.claims) == 3
    assert len(snapshot.payments) == 2
    assert [(source.service, source.records) for source in snapshot.sources] == [
        ("polizas", 3),
        ("primas", 3),
        ("siniestros", 5),
    ]
    requests = identity_server.requests
    assert [request["path"] for request in requests] == [POLICIES_PATH, PREMIUMS_PATH, CLAIMS_PATH]
    assert requests[2]["body"] == {"corte": CUT_OFF_TEXT}
    for request in requests:
        headers = {name.lower(): value for name, value in request["headers"].items()}
        assert headers["authorization"] == "Bearer " + TOKEN
        assert headers["x-correlation-id"] == "corr-1"


def _with_pii() -> dict[str, Any]:
    records = policies()
    records[0]["clienteId"] = "2923bf51-1899-4a3e-9547-b8ccb9c2c84f"
    return policies_payload(records)


def _bad_fingerprint() -> dict[str, Any]:
    payload = premiums_payload()
    payload["huella"] = "0" * 64
    return payload


def _other_cut_off() -> dict[str, Any]:
    payload = policies_payload()
    payload["corte"] = "2026-10-14T10:59:59Z"
    return payload


def _wrong_count() -> dict[str, Any]:
    payload = claims_payload()
    payload["registros"] = 4
    return payload


def _duplicate_claim() -> dict[str, Any]:
    records = claims()
    return claims_payload(records + [records[0]])


def _payment_after_cut_off() -> dict[str, Any]:
    records = payments()
    records[0]["creadoEn"] = "2026-10-14T11:00:01Z"
    return claims_payload(payment_records=records)


def _premium_with_bad_amount() -> dict[str, Any]:
    records = premiums()
    records[0]["monto"] = "12.5"
    return premiums_payload(records)


@pytest.mark.parametrize(
    ("source", "build"),
    [
        ("polizas", lambda: {"policies_body": _with_pii()}),
        ("primas", lambda: {"premiums_body": _bad_fingerprint()}),
        ("polizas", lambda: {"policies_body": _other_cut_off()}),
        ("siniestros", lambda: {"claims_body": _wrong_count()}),
        ("siniestros", lambda: {"claims_body": _duplicate_claim()}),
        ("siniestros", lambda: {"claims_body": _payment_after_cut_off()}),
        ("primas", lambda: {"premiums_body": _premium_with_bad_amount()}),
    ],
)
def test_respuesta_inconsistente_falla_cerrado(
    identity_server: IdentityServer, source: str, build: Callable[[], dict[str, Any]]
) -> None:
    serve(identity_server, **build())

    with pytest.raises(ReportSourceUnavailableError) as error:
        sources(identity_server).fetch(CUT_OFF, "corr-1")

    assert error.value.source == source


def test_error_http_de_una_fuente_falla_cerrado(identity_server: IdentityServer) -> None:
    serve(identity_server)
    identity_server.respond("POST", CLAIMS_PATH, 503, {"code": "siniestro_no_integro"})

    with pytest.raises(ReportSourceUnavailableError) as error:
        sources(identity_server).fetch(CUT_OFF, "corr-1")

    assert error.value.source == "siniestros"


def test_respuesta_no_json_falla_cerrado(identity_server: IdentityServer) -> None:
    serve(identity_server)
    identity_server.respond_raw("GET", PREMIUMS_PATH, 200, b"<html>")

    with pytest.raises(ReportSourceUnavailableError) as error:
        sources(identity_server).fetch(CUT_OFF, "corr-1")

    assert error.value.source == "primas"


def test_fuente_sin_configurar_no_se_consulta(identity_server: IdentityServer) -> None:
    with pytest.raises(ReportSourceUnavailableError) as error:
        sources(identity_server, policies="").fetch(CUT_OFF, "corr-1")

    assert error.value.source == "polizas"
    assert identity_server.requests == []


def test_sin_token_interno_no_se_consulta(identity_server: IdentityServer) -> None:
    gateway = HttpReportSources(identity_server.url, identity_server.url, identity_server.url, "")

    with pytest.raises(ReportSourceUnavailableError):
        gateway.fetch(CUT_OFF, "corr-1")

    assert identity_server.requests == []


def test_fuente_inalcanzable_falla_cerrado() -> None:
    gateway = HttpReportSources("http://127.0.0.1:9", "http://127.0.0.1:9", "", TOKEN, 0.2)

    with pytest.raises(ReportSourceUnavailableError) as error:
        gateway.fetch(CUT_OFF, "corr-1")

    assert error.value.source == "polizas"
