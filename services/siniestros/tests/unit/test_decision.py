from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError
from siniestros.domain.decision import (
    TRANSICIONES,
    DecisionRevision,
    ResultadoEvaluacion,
    TransicionNoPermitida,
    dictaminar,
    dictaminar_revision,
    ruta,
    validar_transicion,
)


def evaluacion(**cambios: object) -> ResultadoEvaluacion:
    datos: dict[str, object] = {
        "evaluacionId": str(uuid4()),
        "evaluadoEn": datetime(2026, 11, 10, 12, tzinfo=UTC).isoformat(),
        "elegible": True,
        "requiereInspeccion": False,
        "fraude": {"nivel": "bajo", "bloqueante": False},
        "reglas": [{"reglaId": "cobertura", "version": 1, "resultado": "cumple"}],
        "motivo": "Evaluacion automatica sin hallazgos.",
    }
    datos.update(cambios)
    return ResultadoEvaluacion.model_validate(datos)


def test_caso_elegible_se_aprueba_automaticamente() -> None:
    dictamen = dictaminar(evaluacion())
    assert (dictamen.resultado, dictamen.estado, dictamen.asignacion) == (
        "aprobado",
        "aprobado",
        None,
    )
    assert dictamen.regla == "aprobacion_automatica"


def test_inspeccion_se_asigna_a_perito_o_a_cola() -> None:
    perito = uuid4()
    con_perito = dictaminar(evaluacion(requiereInspeccion=True, peritoId=str(perito)))
    sin_perito = dictaminar(evaluacion(requiereInspeccion=True))
    assert con_perito.estado == sin_perito.estado == "en_inspeccion"
    assert con_perito.asignacion is not None and sin_perito.asignacion is not None
    assert (con_perito.asignacion.tipo, con_perito.asignacion.destino) == ("perito", str(perito))
    assert (sin_perito.asignacion.tipo, sin_perito.asignacion.destino) == ("cola", "inspeccion")


@pytest.mark.parametrize(
    "fraude",
    [{"nivel": "alto", "bloqueante": False}, {"nivel": "bajo", "bloqueante": True}],
)
def test_fraude_bloqueante_nunca_aprueba_y_prevalece(fraude: dict[str, object]) -> None:
    dictamen = dictaminar(evaluacion(fraude=fraude, requiereInspeccion=True))
    assert dictamen.resultado == "bloqueado_fraude"
    assert dictamen.estado == "en_revision"
    assert dictamen.asignacion is not None and dictamen.asignacion.destino == "fraude"


@pytest.mark.parametrize(
    ("cambios", "regla"),
    [
        ({"fraude": {"nivel": "medio", "bloqueante": False}}, "fraude_riesgo_medio"),
        ({"elegible": False}, "no_elegible_automatico"),
        ({"requiereRevisionHumana": True}, "revision_humana_requerida"),
    ],
)
def test_casos_no_aprobables_van_a_cola_de_revision(cambios: dict[str, object], regla: str) -> None:
    dictamen = dictaminar(evaluacion(**cambios))
    assert dictamen.estado == "en_revision"
    assert dictamen.regla == regla
    assert dictamen.asignacion is not None
    assert (dictamen.asignacion.tipo, dictamen.asignacion.destino) == ("cola", "revision")


def test_maquina_de_estados_rechaza_omitir_transiciones_obligatorias() -> None:
    with pytest.raises(TransicionNoPermitida):
        validar_transicion("reportado", "aprobado")
    with pytest.raises(TransicionNoPermitida):
        ruta("en_revision", "pagado")
    assert ruta("reportado", "aprobado", ("en_evaluacion",)) == ("en_evaluacion", "aprobado")
    assert all(
        destino in TRANSICIONES for destinos in TRANSICIONES.values() for destino in destinos
    )


def test_decision_humana_solo_sobre_casos_enrutados() -> None:
    decision = DecisionRevision.model_validate(
        {"revisorId": str(uuid4()), "resultado": "aprobado", "motivo": "Soportes verificados."}
    )
    assert dictaminar_revision("en_revision", decision).estado == "aprobado"
    with pytest.raises(TransicionNoPermitida):
        dictaminar_revision("en_evaluacion", decision)


def test_perito_solo_aplica_a_inspeccion() -> None:
    with pytest.raises(ValidationError):
        DecisionRevision.model_validate(
            {
                "revisorId": str(uuid4()),
                "resultado": "rechazado",
                "motivo": "Documentos inconsistentes.",
                "peritoId": str(uuid4()),
            }
        )


def test_resultado_de_evaluacion_es_estricto() -> None:
    with pytest.raises(ValidationError):
        evaluacion(reglas=[])
    with pytest.raises(ValidationError):
        evaluacion(elegible="si")
    with pytest.raises(ValidationError):
        evaluacion(evaluadoEn="2026-11-10")
