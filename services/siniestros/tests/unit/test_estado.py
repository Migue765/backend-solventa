from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from siniestros.domain.estado import (
    CambioEstado,
    RegistroSiniestro,
    SiniestroNoIntegro,
    consultar_estado,
)

REPORTE = datetime(2026, 11, 1, 15, 0, tzinfo=UTC)


def registro(estado: str, actualizado_en: datetime = REPORTE, **cambios: Any) -> RegistroSiniestro:
    base = RegistroSiniestro(
        uuid4(),
        uuid4(),
        "asistido",
        estado,
        REPORTE - timedelta(hours=5),
        REPORTE,
        actualizado_en,
    )
    return replace(base, **cambios)


def test_historial_permitido_oculta_cambios_internos_y_conserva_el_vigente() -> None:
    cambios = [
        CambioEstado("reportado", REPORTE, True),
        CambioEstado("en_evaluacion", REPORTE + timedelta(hours=1), True),
        CambioEstado("en_revision", REPORTE + timedelta(hours=2), False),
        CambioEstado("en_inspeccion", REPORTE + timedelta(hours=3), False),
    ]
    consulta = consultar_estado(registro("en_inspeccion", cambios[-1].ocurrido_en), cambios)

    assert [cambio.estado for cambio in consulta.historial] == [
        "reportado",
        "en_evaluacion",
        "en_inspeccion",
    ]
    assert consulta.siniestro.actualizado_en == REPORTE + timedelta(hours=3)


def test_instantes_sin_zona_se_interpretan_en_utc() -> None:
    sin_zona = REPORTE.replace(tzinfo=None)
    consulta = consultar_estado(
        registro("reportado", sin_zona, reportado_en=sin_zona, ocurrido_en=sin_zona),
        [CambioEstado("reportado", sin_zona, True)],
    )

    assert consulta.siniestro.reportado_en == REPORTE
    assert consulta.siniestro.ocurrido_en == REPORTE
    assert consulta.historial[0].ocurrido_en.tzinfo == UTC


@pytest.mark.parametrize(
    ("estado", "cambios"),
    [
        ("reportado", []),
        ("desconocido", [CambioEstado("desconocido", REPORTE, True)]),
        ("aprobado", [CambioEstado("aprobado", REPORTE, True)]),
        (
            "aprobado",
            [
                CambioEstado("reportado", REPORTE, True),
                CambioEstado("en_evaluacion", REPORTE + timedelta(hours=1), True),
            ],
        ),
        (
            "aprobado",
            [
                CambioEstado("reportado", REPORTE, True),
                CambioEstado("en_evaluacion", REPORTE + timedelta(hours=2), True),
                CambioEstado("aprobado", REPORTE + timedelta(hours=1), True),
            ],
        ),
    ],
    ids=["sin-historial", "estado-desconocido", "sin-reporte", "vigente-distinto", "desordenado"],
)
def test_registro_incoherente_no_se_presenta_como_estado_valido(
    estado: str, cambios: list[CambioEstado]
) -> None:
    with pytest.raises(SiniestroNoIntegro):
        consultar_estado(registro(estado, cambios[-1].ocurrido_en if cambios else REPORTE), cambios)


def test_fecha_de_actualizacion_debe_coincidir_con_el_ultimo_cambio() -> None:
    cambios = [CambioEstado("reportado", REPORTE, True)]

    with pytest.raises(SiniestroNoIntegro):
        consultar_estado(registro("reportado", REPORTE + timedelta(1)), cambios)


@pytest.mark.parametrize(
    "cambios",
    [{"tipo": "otro"}, {"ocurrido_en": REPORTE + timedelta(minutes=1)}],
    ids=["tipo-desconocido", "evento-posterior-al-reporte"],
)
def test_datos_del_agregado_incoherentes(cambios: dict[str, Any]) -> None:
    with pytest.raises(SiniestroNoIntegro):
        consultar_estado(
            registro("reportado", **cambios), [CambioEstado("reportado", REPORTE, True)]
        )


def test_acepta_siniestro_parametrico() -> None:
    consulta = consultar_estado(
        registro("reportado", tipo="parametrico"), [CambioEstado("reportado", REPORTE, True)]
    )

    assert consulta.siniestro.tipo == "parametrico"
