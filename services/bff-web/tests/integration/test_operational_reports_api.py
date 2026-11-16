import json
from typing import Any
from uuid import UUID

import pytest
from bff_web.domain.auth import User
from flask import Flask
from flask.testing import FlaskClient
from report_support import CUT_OFF, CUT_OFF_TEXT, FakeReportSources
from support import USER, KeyPair, sign_token

PATH = "/v1/reportes/operativos"
QUERY = {"desde": "2026-10-01", "hasta": "2026-10-14", "corte": CUT_OFF_TEXT}
CORRELATION_ID = "5d1f0e2e-2b50-4f05-8f43-6f2b8a1f2c11"
ADVISOR = User(
    UUID("6b3c3b46-61b6-4d4e-8d4b-0b2f7b0d8a10"),
    "Laura Asesora",
    "laura.asesora@solventa.co",
    "asesor",
)
PARTNER = User(
    UUID("7c4d4c57-72c7-4e5f-9e5c-1c3f8c1e9b21"),
    "Banco Aliado",
    "socio@banco.test",
    "socio",
)


@pytest.fixture
def sources(app: Flask) -> FakeReportSources:
    fake = FakeReportSources()
    app.extensions["report_sources"] = fake
    return fake


def advisor(keypair: KeyPair) -> dict[str, str]:
    token = sign_token(keypair, user=ADVISOR, scope="asesor", socios=["BANCO-001"])
    return {"Authorization": "Bearer " + token, "X-Correlation-ID": CORRELATION_ID}


def events(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    return [
        json.loads(record.getMessage())
        for record in caplog.records
        if getattr(record, "event", None) == "sol322.reporte"
    ]


def test_asesor_consulta_el_reporte_consolidado(
    app: Flask, keypair: KeyPair, sources: FakeReportSources, caplog: pytest.LogCaptureFixture
) -> None:
    response = app.test_client().get(PATH, query_string=QUERY, headers=advisor(keypair))

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    body = response.get_json()
    assert body["corte"] == CUT_OFF_TEXT
    assert body["generadoEn"] == "2026-10-14T12:00:00Z"
    assert body["indicadores"]["polizas"]["cartera"] == 3
    assert body["indicadores"]["siniestros"]["reportados"] == 3
    assert len(body["huella"]) == 64
    assert sources.calls == [(CUT_OFF, CORRELATION_ID)]
    assert "2923bf51-1899-4a3e-9547-b8ccb9c2c84f" not in response.get_data(as_text=True)
    [event] = events(caplog)
    assert event == {
        "event": "sol322.reporte",
        "accion": "consulta",
        "status": 200,
        "code": "ok",
        "correlationId": CORRELATION_ID,
        "endpoint": "operational_reports.report",
        "actorId": str(ADVISOR.user_id),
        "filtros": {
            "desde": "2026-10-01",
            "hasta": "2026-10-14",
            "producto": None,
            "estado": None,
        },
        "corte": CUT_OFF_TEXT,
        "huella": body["huella"],
    }


def test_sin_corte_se_fija_uno_anterior_al_instante_actual(
    app: Flask, keypair: KeyPair, sources: FakeReportSources
) -> None:
    response = app.test_client().get(
        PATH, query_string={"desde": "2026-10-01", "hasta": "2026-10-14"}, headers=advisor(keypair)
    )

    assert response.status_code == 200
    assert response.get_json()["corte"] == "2026-10-14T11:59:00Z"


def test_sin_sesion_se_rechaza_sin_consultar_fuentes(
    app: Flask, sources: FakeReportSources, caplog: pytest.LogCaptureFixture
) -> None:
    response = app.test_client().get(PATH, query_string=QUERY)

    assert response.status_code == 401
    assert response.get_json()["code"] == "sesion_invalida"
    assert sources.calls == []
    assert events(caplog)[0]["code"] == "sesion_invalida"


@pytest.mark.parametrize(
    ("user", "scope"),
    [(USER, "cliente"), (PARTNER, "socio"), (USER, "asesor")],
)
def test_usuario_sin_alcance_de_reportes_se_rechaza(
    app: Flask,
    keypair: KeyPair,
    sources: FakeReportSources,
    caplog: pytest.LogCaptureFixture,
    user: User,
    scope: str,
) -> None:
    token = sign_token(keypair, user=user, scope=scope, socios=["BANCO-001"])

    response = app.test_client().get(
        PATH, query_string=QUERY, headers={"Authorization": "Bearer " + token}
    )

    assert response.status_code == 403
    assert response.get_json()["code"] == "alcance_insuficiente"
    assert sources.calls == []
    [event] = events(caplog)
    assert event["actorId"] == str(user.user_id)
    assert event["status"] == 403


@pytest.mark.parametrize(
    "query",
    [
        {**QUERY, "clienteId": "2923bf51-1899-4a3e-9547-b8ccb9c2c84f"},
        {**QUERY, "producto": "hogar"},
        {**QUERY, "estado": "cancelada"},
        {**QUERY, "corte": "2026-10-14T12:30:00Z"},
        {"desde": "2026-10-01"},
        {"desde": "2025-01-01", "hasta": "2026-10-13"},
    ],
)
def test_filtros_no_autorizados_se_rechazan(
    app: Flask, keypair: KeyPair, sources: FakeReportSources, query: dict[str, str]
) -> None:
    response = app.test_client().get(PATH, query_string=query, headers=advisor(keypair))

    assert response.status_code == 400
    assert response.get_json()["code"] == "solicitud_invalida"
    assert sources.calls == []


def test_filtro_por_producto_y_estado(
    app: Flask, keypair: KeyPair, sources: FakeReportSources
) -> None:
    query = {**QUERY, "producto": "travel", "estado": "vigente"}

    response = app.test_client().get(PATH, query_string=query, headers=advisor(keypair))

    body = response.get_json()
    assert response.status_code == 200
    assert body["filtros"]["producto"] == "travel"
    assert body["indicadores"]["polizas"]["cartera"] == 1
    assert [item["producto"] for item in body["porProducto"]] == ["travel"]


def test_fuente_no_disponible_no_entrega_datos_parciales(
    app: Flask, keypair: KeyPair, caplog: pytest.LogCaptureFixture
) -> None:
    app.extensions["report_sources"] = FakeReportSources(failing_source="siniestros")

    response = app.test_client().get(PATH, query_string=QUERY, headers=advisor(keypair))

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "5"
    body = response.get_json()
    assert body["code"] == "consolidacion_incompleta"
    assert body["fuente"] == "siniestros"
    assert "indicadores" not in body
    [event] = events(caplog)
    assert event["fuente"] == "siniestros"
    assert "huella" not in event


def test_descarga_reproduce_el_reporte_confirmado(
    app: Flask, keypair: KeyPair, sources: FakeReportSources, caplog: pytest.LogCaptureFixture
) -> None:
    client = app.test_client()
    report = client.get(PATH, query_string=QUERY, headers=advisor(keypair)).get_json()

    response = client.get(
        PATH + "/descarga",
        query_string={**QUERY, "huella": report["huella"]},
        headers=advisor(keypair),
    )

    assert response.status_code == 200
    assert response.mimetype == "text/csv"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Reporte-Huella"] == report["huella"]
    assert response.headers["Content-Disposition"] == (
        f'attachment; filename="reporte-operativo-{report["huella"][:12]}.csv"'
    )
    content = response.get_data(as_text=True)
    assert f"reporte,huella,,,{report['huella']}" in content
    assert "primas,total,,COP,1200.50" in content
    assert [event["accion"] for event in events(caplog)] == ["consulta", "descarga"]


def test_descarga_con_huella_distinta_se_rechaza(
    app: Flask, keypair: KeyPair, sources: FakeReportSources
) -> None:
    response = _download(app, keypair, {**QUERY, "huella": "f" * 64})

    assert response.status_code == 409
    assert response.get_json()["code"] == "reporte_no_reproducible"


@pytest.mark.parametrize(
    "query",
    [
        {"desde": "2026-10-01", "hasta": "2026-10-14", "huella": "f" * 64},
        QUERY,
    ],
)
def test_descarga_requiere_corte_y_huella(
    app: Flask, keypair: KeyPair, sources: FakeReportSources, query: dict[str, str]
) -> None:
    response = _download(app, keypair, query)

    assert response.status_code == 400
    assert sources.calls == []


def test_descarga_no_disponible_si_falla_una_fuente(app: Flask, keypair: KeyPair) -> None:
    app.extensions["report_sources"] = FakeReportSources(failing_source="primas")

    response = _download(app, keypair, {**QUERY, "huella": "f" * 64})

    assert response.status_code == 503
    assert response.mimetype == "application/problem+json"


def test_catalogo_de_filtros_permitidos(app: Flask, keypair: KeyPair) -> None:
    client: FlaskClient = app.test_client()

    response = client.get(PATH + "/opciones", headers=advisor(keypair))
    denied = client.get(PATH + "/opciones")

    assert response.status_code == 200
    assert response.get_json() == {
        "productos": [
            "automotive",
            "travel",
            "life",
            "device-protection",
            "parametric",
            "parametric-weather",
            "payment-protection",
            "mortgage-life",
        ],
        "estados": ["programada", "vigente", "vencida"],
        "periodoMaximoDias": 366,
    }
    assert denied.status_code == 401


def _download(app: Flask, keypair: KeyPair, query: dict[str, str]) -> Any:
    return app.test_client().get(PATH + "/descarga", query_string=query, headers=advisor(keypair))
