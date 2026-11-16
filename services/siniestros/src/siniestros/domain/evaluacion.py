"""HU26 (SOL-178): evaluacion automatica y determinista de siniestros.

El evaluador es una funcion pura: recibe un conjunto de reglas versionado y las entradas
relevantes ya calculadas, y devuelve siempre el mismo resultado ante los mismos datos.
No decide aprobaciones ni enruta estados; eso lo hace HU27 (SOL-179) a partir de
``decision_automatica``.
"""

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Annotated, Any, Literal, Self

from pydantic import Field, StrictBool, StrictInt, StringConstraints, model_validator

from siniestros.domain import Input, Timestamp

Tratamiento = Literal["aprobacion", "inspeccion", "rechazo", "revision_humana"]
Resultado = Literal["decidido", "informacion_insuficiente", "pendiente_revision"]
NivelFraude = Literal["bajo", "medio", "alto"]

# Ante varias reglas aplicables prevalece el tratamiento mas conservador.
PRECEDENCIA: dict[str, int] = {
    "aprobacion": 0,
    "inspeccion": 1,
    "rechazo": 2,
    "revision_humana": 3,
}
NIVELES_FRAUDE: dict[str, int] = {"bajo": 1, "medio": 2, "alto": 3}
CAMPOS_NUMERICOS = frozenset(
    {
        "diasHastaReporte",
        "longitudDescripcion",
        "cantidadEvidencias",
        "cantidadEvidenciasConUbicacion",
        "nivelFraude",
        "cantidadSenalesFraude",
    }
)
CAMPOS_TEXTO = frozenset({"tipoEvento"})
CAMPOS_BOOLEANOS = frozenset({"senalesFraudeDisponibles"})

CodigoRegla = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{0,49}$")]
Motivo = Annotated[str, StringConstraints(min_length=10, max_length=300)]
TextoRegla = Annotated[str, StringConstraints(min_length=1, max_length=100)]


class ReglaEvaluacion(Input):
    codigo: CodigoRegla
    campo: Literal[
        "tipoEvento",
        "diasHastaReporte",
        "longitudDescripcion",
        "cantidadEvidencias",
        "cantidadEvidenciasConUbicacion",
        "senalesFraudeDisponibles",
        "nivelFraude",
        "cantidadSenalesFraude",
    ]
    operador: Literal["eq", "neq", "gt", "gte", "lt", "lte", "in"]
    valor: StrictBool | StrictInt | TextoRegla | list[StrictInt] | list[TextoRegla]
    tratamiento: Tratamiento
    motivo: Motivo

    @model_validator(mode="after")
    def tipos_compatibles(self) -> Self:
        valores = self.valor if isinstance(self.valor, list) else [self.valor]
        if self.operador == "in":
            if not isinstance(self.valor, list) or not 1 <= len(self.valor) <= 50:
                raise ValueError("El operador in requiere entre 1 y 50 valores.")
        elif isinstance(self.valor, list):
            raise ValueError("Solo el operador in admite listas.")
        if self.campo in CAMPOS_BOOLEANOS:
            if self.operador not in {"eq", "neq"} or not isinstance(self.valor, bool):
                raise ValueError("Un campo booleano solo admite eq/neq con true o false.")
        elif self.campo in CAMPOS_TEXTO:
            if self.operador not in {"eq", "neq", "in"} or not all(
                isinstance(valor, str) for valor in valores
            ):
                raise ValueError("Un campo de texto solo admite eq/neq/in con texto.")
        elif not all(isinstance(valor, int) and not isinstance(valor, bool) for valor in valores):
            raise ValueError("Un campo numerico requiere valores enteros.")
        return self


class ConjuntoReglasEvaluacion(Input):
    version: int = Field(ge=1)
    vigenteDesde: Timestamp
    vigenteHasta: Timestamp | None = None
    evidenciaMinima: int = Field(default=1, ge=0, le=100)
    longitudMinimaDescripcion: int = Field(default=10, ge=10, le=5000)
    nivelFraudeRevisionHumana: NivelFraude | None = "medio"
    tratamientoPorDefecto: Tratamiento
    motivoPorDefecto: Motivo
    # HU27 acepta hasta 50 reglas por decision: 49 publicadas mas FRAUDE_NIVEL.
    reglas: list[ReglaEvaluacion] = Field(default_factory=list, max_length=49)

    @model_validator(mode="after")
    def codigos_unicos(self) -> Self:
        codigos = [regla.codigo for regla in self.reglas]
        if len(codigos) != len(set(codigos)) or set(codigos) & CODIGOS_RESERVADOS:
            raise ValueError("Los codigos de regla deben ser unicos y no reservados.")
        return self


class SenalFraude(Input):
    codigo: TextoRegla
    nivel: NivelFraude
    fuente: TextoRegla


class SolicitudEvaluacion(Input):
    """``senalesFraude`` ausente significa que no hay senales disponibles para el caso."""

    senalesFraude: list[SenalFraude] | None = Field(default=None, max_length=50)


@dataclass(frozen=True)
class EntradasEvaluacion:
    siniestroId: str
    polizaId: str
    tipoEvento: str
    fechaEvento: str
    reportadoEn: str
    diasHastaReporte: int
    longitudDescripcion: int
    cantidadEvidencias: int
    cantidadEvidenciasConUbicacion: int
    evidencias: tuple[dict[str, str], ...]
    senalesFraudeDisponibles: bool
    senalesFraude: tuple[dict[str, str], ...]
    nivelFraude: int
    cantidadSenalesFraude: int

    def como_dict(self) -> dict[str, Any]:
        datos = asdict(self)
        datos["evidencias"] = list(self.evidencias)
        datos["senalesFraude"] = list(self.senalesFraude)
        return datos


@dataclass(frozen=True)
class ReglaAplicada:
    codigo: str
    tratamiento: Tratamiento | None
    motivo: str


@dataclass(frozen=True)
class ResultadoEvaluacion:
    resultado: Resultado
    tratamiento: Tratamiento | None
    requiere_revision_humana: bool
    reglas_aplicadas: tuple[ReglaAplicada, ...]
    motivo: str


INFORMACION_MINIMA = "INFORMACION_MINIMA"
FRAUDE_NIVEL = "FRAUDE_NIVEL"
SIN_REGLAS_VIGENTES = "SIN_REGLAS_VIGENTES"
FALLO_EVALUADOR = "FALLO_EVALUADOR"
SINIESTRO_NO_INTEGRO = "SINIESTRO_NO_INTEGRO"
TRATAMIENTO_POR_DEFECTO = "TRATAMIENTO_POR_DEFECTO"
CODIGOS_RESERVADOS = frozenset(
    {
        INFORMACION_MINIMA,
        FRAUDE_NIVEL,
        SIN_REGLAS_VIGENTES,
        FALLO_EVALUADOR,
        SINIESTRO_NO_INTEGRO,
        TRATAMIENTO_POR_DEFECTO,
    }
)
# Version de las reglas de sistema (codigos reservados) cuando no hay conjunto vigente.
VERSION_REGLAS_SISTEMA = 1
MOTIVOS_PENDIENTE = {
    SIN_REGLAS_VIGENTES: "No hay una version de reglas vigente; el caso queda pendiente de "
    "revision humana.",
    FALLO_EVALUADOR: "El evaluador no pudo decidir; el caso queda pendiente de revision humana.",
    SINIESTRO_NO_INTEGRO: "El registro del siniestro no es integro; queda pendiente de revision "
    "humana sin cambiar su estado.",
}


def nivel_fraude(senales: list[SenalFraude] | None) -> int:
    return max((NIVELES_FRAUDE[senal.nivel] for senal in senales or []), default=0)


def evaluar(
    conjunto: ConjuntoReglasEvaluacion, entradas: EntradasEvaluacion
) -> ResultadoEvaluacion:
    faltantes = []
    if entradas.cantidadEvidencias < conjunto.evidenciaMinima:
        faltantes.append(
            f"se requieren al menos {conjunto.evidenciaMinima} evidencias confirmadas "
            f"y hay {entradas.cantidadEvidencias}"
        )
    if entradas.longitudDescripcion < conjunto.longitudMinimaDescripcion:
        faltantes.append(
            f"la descripcion requiere al menos {conjunto.longitudMinimaDescripcion} caracteres"
        )
    if faltantes:
        motivo = "Informacion minima insuficiente: " + "; ".join(faltantes) + "."
        return ResultadoEvaluacion(
            "informacion_insuficiente",
            None,
            False,
            (ReglaAplicada(INFORMACION_MINIMA, None, motivo),),
            motivo,
        )

    valores = entradas.como_dict()
    aplicadas: list[ReglaAplicada] = []
    umbral = conjunto.nivelFraudeRevisionHumana
    if umbral is not None and entradas.nivelFraude >= NIVELES_FRAUDE[umbral]:
        aplicadas.append(
            ReglaAplicada(
                FRAUDE_NIVEL,
                "revision_humana",
                f"Hay senales de fraude de nivel {umbral} o superior.",
            )
        )
    aplicadas.extend(
        ReglaAplicada(regla.codigo, regla.tratamiento, regla.motivo)
        for regla in conjunto.reglas
        if cumple(regla, valores)
    )
    if not aplicadas:
        return ResultadoEvaluacion(
            "decidido",
            conjunto.tratamientoPorDefecto,
            conjunto.tratamientoPorDefecto == "revision_humana",
            (),
            conjunto.motivoPorDefecto,
        )
    tratamiento: Tratamiento = max(
        (regla.tratamiento for regla in aplicadas if regla.tratamiento is not None),
        key=PRECEDENCIA.__getitem__,
    )
    return ResultadoEvaluacion(
        "decidido",
        tratamiento,
        tratamiento == "revision_humana",
        tuple(aplicadas),
        " ".join(regla.motivo for regla in aplicadas),
    )


def pendiente(codigo: str) -> ResultadoEvaluacion:
    motivo = MOTIVOS_PENDIENTE[codigo]
    return ResultadoEvaluacion(
        "pendiente_revision",
        "revision_humana",
        True,
        (ReglaAplicada(codigo, "revision_humana", motivo),),
        motivo,
    )


def cumple(regla: ReglaEvaluacion, valores: Mapping[str, Any]) -> bool:
    actual = valores[regla.campo]
    esperado = regla.valor
    if regla.operador == "in":
        return actual in esperado  # type: ignore[operator]
    if regla.operador == "eq":
        return bool(actual == esperado)
    if regla.operador == "neq":
        return bool(actual != esperado)
    comparadores = {
        "gt": int.__gt__,
        "gte": int.__ge__,
        "lt": int.__lt__,
        "lte": int.__le__,
    }
    return bool(comparadores[regla.operador](int(actual), int(esperado)))  # type: ignore[arg-type]
