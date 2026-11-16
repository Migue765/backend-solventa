from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError
from siniestros.domain.evaluacion import (
    FRAUDE_NIVEL,
    INFORMACION_MINIMA,
    ConjuntoReglasEvaluacion,
    EntradasEvaluacion,
    SenalFraude,
    evaluar,
    nivel_fraude,
)


def conjunto(**cambios: Any) -> ConjuntoReglasEvaluacion:
    datos: dict[str, Any] = {
        "version": 1,
        "vigenteDesde": "2026-01-01T00:00:00Z",
        "tratamientoPorDefecto": "aprobacion",
        "motivoPorDefecto": "Cumple las condiciones para aprobacion automatica.",
        "reglas": [
            {
                "codigo": "REPORTE_TARDIO",
                "campo": "diasHastaReporte",
                "operador": "gt",
                "valor": 30,
                "tratamiento": "inspeccion",
                "motivo": "El reporte supera 30 dias desde el evento.",
            },
            {
                "codigo": "EVENTO_EXCLUIDO",
                "campo": "tipoEvento",
                "operador": "in",
                "valor": ["Guerra"],
                "tratamiento": "rechazo",
                "motivo": "El tipo de evento esta excluido.",
            },
        ],
    }
    datos.update(cambios)
    return ConjuntoReglasEvaluacion.model_validate(datos)


def entradas(**cambios: Any) -> EntradasEvaluacion:
    datos: dict[str, Any] = {
        "siniestroId": "s",
        "polizaId": "p",
        "tipoEvento": "Accidente",
        "fechaEvento": "2026-10-31T15:00:00Z",
        "reportadoEn": "2026-11-01T15:00:00Z",
        "diasHastaReporte": 1,
        "longitudDescripcion": 40,
        "cantidadEvidencias": 2,
        "cantidadEvidenciasConUbicacion": 1,
        "evidencias": (),
        "senalesFraudeDisponibles": False,
        "senalesFraude": (),
        "nivelFraude": 0,
        "cantidadSenalesFraude": 0,
    }
    datos.update(cambios)
    return EntradasEvaluacion(**datos)


def test_sin_reglas_aplicables_usa_tratamiento_por_defecto() -> None:
    resultado = evaluar(conjunto(), entradas())

    assert resultado.resultado == "decidido"
    assert resultado.tratamiento == "aprobacion"
    assert not resultado.requiere_revision_humana
    assert resultado.reglas_aplicadas == ()


def test_misma_entrada_y_version_dan_el_mismo_resultado() -> None:
    reglas = conjunto()
    datos = entradas(diasHastaReporte=45, tipoEvento="Guerra")

    assert {evaluar(reglas, datos) for _ in range(20)} == {evaluar(reglas, datos)}


def test_prevalece_el_tratamiento_mas_conservador() -> None:
    resultado = evaluar(conjunto(), entradas(diasHastaReporte=45, tipoEvento="Guerra"))

    assert resultado.tratamiento == "rechazo"
    assert [regla.codigo for regla in resultado.reglas_aplicadas] == [
        "REPORTE_TARDIO",
        "EVENTO_EXCLUIDO",
    ]
    assert "30 dias" in resultado.motivo and "excluido" in resultado.motivo


def test_informacion_minima_faltante_no_decide() -> None:
    resultado = evaluar(conjunto(evidenciaMinima=3), entradas(longitudDescripcion=5))

    assert resultado.resultado == "informacion_insuficiente"
    assert resultado.tratamiento is None
    assert resultado.reglas_aplicadas[0].codigo == INFORMACION_MINIMA
    assert "3 evidencias" in resultado.motivo and "descripcion" in resultado.motivo


def test_senales_de_fraude_desde_umbral_requieren_revision_humana() -> None:
    resultado = evaluar(conjunto(), entradas(nivelFraude=2, senalesFraudeDisponibles=True))

    assert resultado.tratamiento == "revision_humana"
    assert resultado.requiere_revision_humana
    assert resultado.reglas_aplicadas[0].codigo == FRAUDE_NIVEL
    assert evaluar(conjunto(), entradas(nivelFraude=1)).tratamiento == "aprobacion"
    assert (
        evaluar(conjunto(nivelFraudeRevisionHumana=None), entradas(nivelFraude=3)).tratamiento
        == "aprobacion"
    )


def test_nivel_fraude_es_el_maximo_de_las_senales() -> None:
    senales = [
        SenalFraude(codigo="A", nivel="medio", fuente="motor"),
        SenalFraude(codigo="B", nivel="alto", fuente="motor"),
    ]

    assert nivel_fraude(senales) == 3
    assert nivel_fraude(None) == 0


@pytest.mark.parametrize(
    "regla",
    [
        {"campo": "diasHastaReporte", "operador": "gt", "valor": "30"},
        {"campo": "tipoEvento", "operador": "gt", "valor": "Robo"},
        {"campo": "senalesFraudeDisponibles", "operador": "gt", "valor": True},
        {"campo": "diasHastaReporte", "operador": "in", "valor": 3},
        {"campo": "diasHastaReporte", "operador": "eq", "valor": [3]},
        {"campo": "diasHastaReporte", "operador": "eq", "valor": True},
    ],
)
def test_rechaza_reglas_con_tipos_incompatibles(regla: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        conjunto(
            reglas=[
                {**regla, "codigo": "R1", "tratamiento": "inspeccion", "motivo": "Motivo."},
            ]
        )


def test_rechaza_codigos_duplicados_o_reservados() -> None:
    regla = {
        "codigo": "R1",
        "campo": "cantidadEvidencias",
        "operador": "lt",
        "valor": 2,
        "tratamiento": "inspeccion",
        "motivo": "Pocas evidencias.",
    }
    with pytest.raises(ValidationError):
        conjunto(reglas=[regla, regla])
    with pytest.raises(ValidationError):
        conjunto(reglas=[{**regla, "codigo": FRAUDE_NIVEL}])


def test_vigencia_se_normaliza_a_utc() -> None:
    reglas = conjunto(vigenteDesde="2026-01-01T05:00:00+05:00")

    assert reglas.vigenteDesde == datetime(2026, 1, 1, tzinfo=UTC)
