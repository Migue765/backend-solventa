"""Motor de deteccion de patrones sospechosos (HU29, ASR-FRD-01).

El dominio es puro: recibe senales numericas del siniestro y las reglas versionadas
vigentes, y produce un resultado explicable. La persistencia y el enrutamiento viven en
``siniestros.infrastructure.fraude``.
"""

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints

from siniestros.domain import Input, Timestamp
from siniestros.domain.parametricos import ValorMetrica, cumple

VERSION_MOTOR = "1.0.0"
NombreSenal = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,59}$")]
Operador = Literal["gt", "gte", "lt", "lte", "eq"]
Politica = Literal["revision_humana", "continuar"]
Prioridad = Literal["alta", "media", "baja"]
RANGO_PRIORIDAD: dict[str, int] = {"alta": 3, "media": 2, "baja": 1}

SENALES_CALCULADAS = frozenset(
    {
        "horas_reporte_tras_evento",
        "siniestros_cliente_90_dias",
        "siniestros_poliza_30_dias",
        "evidencias_confirmadas",
        "evidencias_duplicadas",
        "evidencias_sin_ubicacion",
    }
)


class ReglaFraude(Input):
    reglaId: UUID
    version: int = Field(ge=1)
    nombre: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    motivo: Annotated[str, StringConstraints(min_length=10, max_length=300)]
    senal: NombreSenal
    operador: Operador
    umbral: ValorMetrica
    peso: int = Field(ge=1, le=100)
    vigenteDesde: Timestamp
    vigenteHasta: Timestamp | None = None


class SolicitudDeteccion(Input):
    senales: dict[NombreSenal, ValorMetrica] = Field(default_factory=dict, max_length=50)


class LimiteRevision(Input):
    limite: int = Field(default=100, ge=1, le=500)


@dataclass(frozen=True)
class ConfiguracionFraude:
    umbral_sospecha: int = 50
    umbral_prioridad_alta: int = 80
    politica_continuidad: Politica = "revision_humana"
    presupuesto_ms: int = 1_000

    def __post_init__(self) -> None:
        if not 1 <= self.umbral_sospecha <= self.umbral_prioridad_alta <= 100:
            raise ValueError("Fraud thresholds must satisfy 1 <= sospecha <= alta <= 100.")
        if self.politica_continuidad not in {"revision_humana", "continuar"}:
            raise ValueError("FRAUDE_POLITICA_CONTINUIDAD must be revision_humana or continuar.")
        if not 0 < self.presupuesto_ms <= 1_000:
            raise ValueError("Fraud detection budget must be between 1 and 1000 ms.")

    @classmethod
    def from_environment(cls) -> "ConfiguracionFraude":
        politica = os.getenv("FRAUDE_POLITICA_CONTINUIDAD", "revision_humana")
        if politica not in {"revision_humana", "continuar"}:
            raise ValueError("FRAUDE_POLITICA_CONTINUIDAD must be revision_humana or continuar.")
        return cls(
            umbral_sospecha=int(os.getenv("FRAUDE_UMBRAL_SOSPECHA", "50")),
            umbral_prioridad_alta=int(os.getenv("FRAUDE_UMBRAL_PRIORIDAD_ALTA", "80")),
            politica_continuidad="continuar" if politica == "continuar" else "revision_humana",
            presupuesto_ms=int(os.getenv("FRAUDE_PRESUPUESTO_MS", "1000")),
        )


@dataclass(frozen=True)
class VersionRegla:
    regla_id: str
    version: int
    nombre: str
    motivo: str
    senal: str
    operador: str
    umbral: Decimal
    peso: int
    vigente_desde: datetime
    vigente_hasta: datetime | None


@dataclass(frozen=True)
class Resultado:
    resultado: Literal["sospechoso", "legitimo"]
    puntaje: int
    prioridad: Prioridad | None
    motivo: str
    evidencia: tuple[dict[str, object], ...]
    reglas: tuple[dict[str, object], ...]

    @property
    def requiere_revision(self) -> bool:
        return self.resultado == "sospechoso"


def vigentes(reglas: Sequence[VersionRegla], instante: datetime) -> list[VersionRegla]:
    """Toma la mayor version de cada regla cuya vigencia cubre el instante."""
    momento = _utc(instante)
    actuales: dict[str, VersionRegla] = {}
    for regla in reglas:
        if _utc(regla.vigente_desde) > momento:
            continue
        if regla.vigente_hasta is not None and momento >= _utc(regla.vigente_hasta):
            continue
        anterior = actuales.get(regla.regla_id)
        if anterior is None or anterior.version < regla.version:
            actuales[regla.regla_id] = regla
    return sorted(actuales.values(), key=lambda regla: (regla.regla_id, regla.version))


def evaluar(
    senales: Mapping[str, Decimal],
    reglas: Sequence[VersionRegla],
    configuracion: ConfiguracionFraude,
) -> Resultado:
    evidencia: list[dict[str, object]] = []
    evaluadas: list[dict[str, object]] = []
    for regla in reglas:
        valor = senales.get(regla.senal)
        activada = valor is not None and cumple(regla.operador, valor, regla.umbral)
        evaluadas.append(
            {
                "reglaId": regla.regla_id,
                "version": regla.version,
                "nombre": regla.nombre,
                "activada": activada,
            }
        )
        if activada:
            evidencia.append(
                {
                    "reglaId": regla.regla_id,
                    "version": regla.version,
                    "senal": regla.senal,
                    "valor": str(valor),
                    "operador": regla.operador,
                    "umbral": str(regla.umbral),
                    "peso": regla.peso,
                    "motivo": regla.motivo,
                }
            )
    puntaje = min(100, sum(int(str(item["peso"])) for item in evidencia))
    if puntaje < configuracion.umbral_sospecha:
        return Resultado(
            "legitimo",
            puntaje,
            None,
            (
                f"Sin senales suficientes: puntaje {puntaje} menor al umbral "
                f"{configuracion.umbral_sospecha}."
            ),
            tuple(evidencia),
            tuple(evaluadas),
        )
    prioridad: Prioridad = "alta" if puntaje >= configuracion.umbral_prioridad_alta else "media"
    motivos = "; ".join(str(item["motivo"]) for item in evidencia)
    return Resultado(
        "sospechoso",
        puntaje,
        prioridad,
        f"Patron sospechoso con puntaje {puntaje}: {motivos}",
        tuple(evidencia),
        tuple(evaluadas),
    )


def _utc(valor: datetime) -> datetime:
    return valor.replace(tzinfo=UTC) if valor.tzinfo is None else valor.astimezone(UTC)
