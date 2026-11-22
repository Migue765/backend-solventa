"""HU27: aprobacion automatica o enrutamiento de siniestros asistidos.

El resultado de evaluacion y fraude llega por una interfaz desacoplada
(``ResultadoEvaluacion``) y la politica vigente decide aprobar, enrutar a
inspeccion o enviar a una cola de revision sin omitir transiciones obligatorias.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictBool, StrictInt, StringConstraints, model_validator

from siniestros.domain import Input, Timestamp

POLITICA = "decision-siniestros"
VERSION_POLITICA = 1

TRANSICIONES: Mapping[str, frozenset[str]] = {
    "reportado": frozenset({"en_evaluacion"}),
    "en_evaluacion": frozenset({"aprobado", "en_revision", "en_inspeccion", "rechazado"}),
    "en_revision": frozenset({"aprobado", "rechazado", "en_inspeccion"}),
    "en_inspeccion": frozenset({"aprobado", "rechazado", "en_revision"}),
    "aprobado": frozenset({"pagado"}),
    "rechazado": frozenset({"cerrado"}),
    "pagado": frozenset({"cerrado"}),
    "cerrado": frozenset(),
}
ESTADOS_DECISION_AUTOMATICA = frozenset({"reportado", "en_evaluacion"})
ESTADOS_DECISION_HUMANA = frozenset({"en_revision", "en_inspeccion"})
COLAS = frozenset({"revision", "inspeccion", "fraude"})

Texto = Annotated[str, StringConstraints(min_length=1, max_length=100)]
Motivo = Annotated[str, StringConstraints(min_length=10, max_length=1000)]
Resultado = Literal["aprobado", "inspeccion", "revision", "bloqueado_fraude", "rechazado"]


class TransicionNoPermitida(Exception):
    """El cambio solicitado omitiria o contradiria la maquina de estados."""


class Fraude(Input):
    nivel: Literal["bajo", "medio", "alto"]
    bloqueante: StrictBool


class ReglaEvaluada(Input):
    reglaId: Texto
    version: StrictInt = Field(ge=1)
    resultado: Texto


class ResultadoEvaluacion(Input):
    """Contrato de entrada producido por la evaluacion automatica (HU26)."""

    evaluacionId: UUID
    evaluadoEn: Timestamp
    elegible: StrictBool
    requiereInspeccion: StrictBool
    fraude: Fraude
    reglas: list[ReglaEvaluada] = Field(min_length=1, max_length=50)
    motivo: Motivo
    peritoId: UUID | None = None
    requiereRevisionHumana: StrictBool = False


class DecisionRevision(Input):
    """Decision humana de un perito o analista sobre un caso enrutado."""

    revisorId: UUID
    resultado: Literal["aprobado", "rechazado", "inspeccion"]
    motivo: Motivo
    peritoId: UUID | None = None

    @model_validator(mode="after")
    def perito_solo_en_inspeccion(self) -> "DecisionRevision":
        if self.peritoId is not None and self.resultado != "inspeccion":
            raise ValueError("El perito solo aplica al enrutar a inspeccion.")
        return self


@dataclass(frozen=True)
class Asignacion:
    tipo: Literal["perito", "cola"]
    destino: str


@dataclass(frozen=True)
class Dictamen:
    resultado: Resultado
    estado: str
    regla: str
    motivo: str
    asignacion: Asignacion | None


def validar_transicion(origen: str, destino: str) -> None:
    if destino not in TRANSICIONES.get(origen, frozenset()):
        raise TransicionNoPermitida(f"No se permite pasar de {origen} a {destino}.")


def ruta(origen: str, destino: str, obligatorios: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Valida cada paso desde ``origen`` hasta ``destino`` pasando por los obligatorios."""
    pasos = (*obligatorios, destino)
    actual = origen
    for paso in pasos:
        validar_transicion(actual, paso)
        actual = paso
    return pasos


def dictaminar(evaluacion: ResultadoEvaluacion) -> Dictamen:
    """Aplica la politica vigente; el fraude bloqueante nunca se aprueba automaticamente."""
    if evaluacion.fraude.bloqueante or evaluacion.fraude.nivel == "alto":
        return Dictamen(
            "bloqueado_fraude",
            "en_revision",
            "fraude_bloqueante",
            "Resultado de fraude bloqueante; requiere revision humana.",
            Asignacion("cola", "fraude"),
        )
    if evaluacion.requiereInspeccion:
        asignacion = (
            Asignacion("perito", str(evaluacion.peritoId))
            if evaluacion.peritoId is not None
            else Asignacion("cola", "inspeccion")
        )
        return Dictamen(
            "inspeccion",
            "en_inspeccion",
            "requiere_inspeccion",
            "La evaluacion exige inspeccion del siniestro.",
            asignacion,
        )
    if evaluacion.requiereRevisionHumana:
        return Dictamen(
            "revision",
            "en_revision",
            "revision_humana_requerida",
            "La evaluacion solicita revision humana.",
            Asignacion("cola", "revision"),
        )
    if evaluacion.fraude.nivel == "medio":
        return Dictamen(
            "revision",
            "en_revision",
            "fraude_riesgo_medio",
            "Riesgo de fraude medio; requiere revision humana.",
            Asignacion("cola", "revision"),
        )
    if not evaluacion.elegible:
        return Dictamen(
            "revision",
            "en_revision",
            "no_elegible_automatico",
            "El caso no es elegible para aprobacion automatica.",
            Asignacion("cola", "revision"),
        )
    return Dictamen(
        "aprobado",
        "aprobado",
        "aprobacion_automatica",
        "Caso elegible sin senales de fraude ni inspeccion pendiente.",
        None,
    )


def dictaminar_revision(origen: str, decision: DecisionRevision) -> Dictamen:
    if origen not in ESTADOS_DECISION_HUMANA:
        raise TransicionNoPermitida("Solo se deciden casos enrutados a revision o inspeccion.")
    if decision.resultado == "inspeccion":
        asignacion = (
            Asignacion("perito", str(decision.peritoId))
            if decision.peritoId is not None
            else Asignacion("cola", "inspeccion")
        )
        return Dictamen(
            "inspeccion", "en_inspeccion", "revision_humana", decision.motivo, asignacion
        )
    return Dictamen(
        decision.resultado, decision.resultado, "revision_humana", decision.motivo, None
    )
