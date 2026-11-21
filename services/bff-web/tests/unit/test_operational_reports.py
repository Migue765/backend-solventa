from datetime import UTC, datetime, timedelta

import pytest
from bff_web.domain.operational_reports import (
    InvalidReportFilterError,
    ReportFilters,
    ReportSourceUnavailableError,
    build_report,
    parse_filters,
    report_csv,
)
from report_support import CUT_OFF, CUT_OFF_TEXT, snapshot

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
GENERATED = datetime(2026, 10, 14, 12, 0, 5, tzinfo=UTC)


def filters(**values: str) -> ReportFilters:
    args = {"desde": "2026-10-01", "hasta": "2026-10-14", "corte": CUT_OFF_TEXT, **values}
    return parse_filters({key: [value] for key, value in args.items()}, NOW)


def test_filtros_validos_normalizan_el_corte_a_utc() -> None:
    parsed = filters(corte="2026-10-14T06:00:00-05:00", producto="travel", estado="vigente")

    assert parsed.cut_off == CUT_OFF
    assert parsed.as_json() == {
        "desde": "2026-10-01",
        "hasta": "2026-10-14",
        "producto": "travel",
        "estado": "vigente",
    }


def test_corte_por_defecto_es_anterior_al_instante_actual() -> None:
    parsed = parse_filters({"desde": ["2026-10-01"], "hasta": ["2026-10-13"]}, NOW)

    assert parsed.cut_off == NOW - timedelta(seconds=60)
    assert parsed.product is None and parsed.state is None


@pytest.mark.parametrize(
    "args",
    [
        {"desde": ["2026-10-01"], "hasta": ["2026-10-14"], "clienteId": ["x"]},
        {"desde": ["2026-10-01", "2026-10-02"], "hasta": ["2026-10-14"]},
        {"hasta": ["2026-10-14"]},
        {"desde": ["2026-10-1"], "hasta": ["2026-10-14"]},
        {"desde": ["2026-10-10"], "hasta": ["2026-10-01"]},
        {"desde": ["2025-10-01"], "hasta": ["2026-10-13"]},
        {"desde": ["2026-10-01"], "hasta": ["2026-10-15"], "corte": ["2026-10-14T11:00:00Z"]},
        {"desde": ["2026-10-01"], "hasta": ["2026-10-14"], "corte": ["2026-10-14T13:00:00Z"]},
        {"desde": ["2026-10-01"], "hasta": ["2026-10-14"], "corte": ["2026-10-14T11:00:00"]},
        {"desde": ["2026-10-01"], "hasta": ["2026-10-14"], "corte": ["ayer"]},
        {"desde": ["2026-10-01"], "hasta": ["2026-10-13"], "producto": ["hogar"]},
        {"desde": ["2026-10-01"], "hasta": ["2026-10-13"], "estado": ["anulada"]},
    ],
)
def test_filtros_no_permitidos_se_rechazan(args: dict[str, list[str]]) -> None:
    with pytest.raises(InvalidReportFilterError):
        parse_filters(args, NOW)


def test_reporte_consolida_indicadores_sin_filtros() -> None:
    report = build_report(filters(), snapshot(), GENERATED)

    indicators = report["indicadores"]
    assert report["corte"] == CUT_OFF_TEXT
    assert report["generadoEn"] == "2026-10-14T12:00:05Z"
    assert indicators["polizas"] == {
        "emitidasEnPeriodo": 2,
        "cartera": 3,
        "porEstado": {"programada": 1, "vigente": 2, "vencida": 0, "cancelada": 0},
    }
    assert indicators["primas"] == {
        "confirmadas": 2,
        "totales": [{"moneda": "COP", "monto": "1200.50"}, {"moneda": "USD", "monto": "100.00"}],
    }
    assert indicators["siniestros"]["reportados"] == 3
    assert indicators["siniestros"]["porEstado"]["en_evaluacion"] == 1
    assert indicators["siniestros"]["porEstado"]["aprobado"] == 1
    assert indicators["siniestros"]["porTipo"] == {"asistido": 1, "parametrico": 1, "sin_tipo": 1}
    assert indicators["pagos"] == {
        "registrados": 2,
        "porEstado": {"pendiente": 1, "confirmado": 1, "rechazado": 0},
        "totales": [
            {"estado": "pendiente", "moneda": "COP", "monto": "10.00"},
            {"estado": "confirmado", "moneda": "COP", "monto": "250000.00"},
        ],
    }
    assert report["inconsistencias"] == {
        "siniestrosSinPoliza": 1,
        "polizasSinPrima": 1,
        "primasConProductoDistinto": 0,
        "pagosSinSiniestro": 0,
    }
    by_product = {item["producto"]: item for item in report["porProducto"]}
    assert len(by_product) == 8
    assert by_product["automotive"]["primasConfirmadas"] == 2
    assert by_product["automotive"]["siniestrosReportados"] == 1
    assert by_product["travel"]["pagosRegistrados"] == 2
    assert [source["servicio"] for source in report["fuentes"]] == [
        "polizas",
        "primas",
        "siniestros",
    ]


def test_filtro_de_producto_restringe_todas_las_dimensiones() -> None:
    report = build_report(filters(producto="travel"), snapshot(), GENERATED)

    indicators = report["indicadores"]
    assert indicators["polizas"]["cartera"] == 1
    assert indicators["polizas"]["emitidasEnPeriodo"] == 0
    assert indicators["primas"] == {"confirmadas": 0, "totales": []}
    assert indicators["siniestros"]["reportados"] == 1
    assert indicators["pagos"]["registrados"] == 2
    assert [item["producto"] for item in report["porProducto"]] == ["travel"]
    assert report["inconsistencias"]["siniestrosSinPoliza"] == 0


def test_filtro_de_estado_usa_el_estado_de_la_poliza_al_corte() -> None:
    report = build_report(filters(estado="programada"), snapshot(), GENERATED)

    indicators = report["indicadores"]
    assert indicators["polizas"]["porEstado"] == {
        "programada": 1,
        "vigente": 0,
        "vencida": 0,
        "cancelada": 0,
    }
    assert indicators["primas"]["confirmadas"] == 0
    assert indicators["siniestros"]["reportados"] == 0
    assert indicators["pagos"]["registrados"] == 0


def test_huella_es_reproducible_y_cambia_con_los_filtros() -> None:
    first = build_report(filters(), snapshot(), GENERATED)
    again = build_report(filters(), snapshot(), GENERATED + timedelta(minutes=5))
    filtered = build_report(filters(producto="life"), snapshot(), GENERATED)

    assert first["huella"] == again["huella"]
    assert first["generadoEn"] != again["generadoEn"]
    assert filtered["huella"] != first["huella"]


def test_reporte_no_mezcla_cortes_distintos() -> None:
    with pytest.raises(ReportSourceUnavailableError):
        build_report(filters(), snapshot(CUT_OFF - timedelta(seconds=1)), GENERATED)


def test_csv_incluye_corte_huella_e_indicadores_sin_identificadores() -> None:
    report = build_report(filters(), snapshot(), GENERATED)

    content = report_csv(report)

    lines = content.splitlines()
    assert lines[0] == "ambito,indicador,dimension,moneda,valor"
    assert f"reporte,corte,,,{CUT_OFF_TEXT}" in lines
    assert f"reporte,huella,,,{report['huella']}" in lines
    assert "filtro,producto,,,todos" in lines
    assert "primas,total,,COP,1200.50" in lines
    assert "pagos,total,confirmado,COP,250000.00" in lines
    assert "inconsistencias,siniestrosSinPoliza,,,1" in lines
    assert "11111111-1111-4111-8111-111111111111" not in content
