from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from bff_web.domain.claims import ClaimDetail, ClaimState

ReviewQueue = Literal["revision", "inspeccion", "fraude"]


class ReviewModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReviewInput(ReviewModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    resultado: Literal["aprobado", "rechazado", "inspeccion"]
    motivo: str = Field(min_length=10, max_length=1000)


class Assignment(ReviewModel):
    id: UUID
    siniestroId: UUID
    tipo: Literal["cola", "perito"]
    destino: str = Field(min_length=1)
    estado: Literal["activa", "cerrada"]
    asignadaEn: AwareDatetime
    cerradaEn: AwareDatetime | None


class QueueAssignment(Assignment):
    estadoSiniestro: ClaimState


class ReviewAssignments(ReviewModel):
    tipo: Literal["cola"]
    destino: ReviewQueue
    asignaciones: list[QueueAssignment]


class DecisionActor(ReviewModel):
    tipo: Literal["sistema", "revisor"]
    id: UUID | None


class DecisionTransition(ReviewModel):
    de: ClaimState
    a: ClaimState
    secuencia: int = Field(ge=2)


class EvaluatedRule(ReviewModel):
    reglaId: str
    version: int = Field(ge=1)
    resultado: str


class DecisionRules(ReviewModel):
    politica: str
    version: int = Field(ge=1)
    aplicada: str
    evaluacion: list[EvaluatedRule]
    motivoEvaluacion: str | None = None


class ClaimDecision(ReviewModel):
    id: UUID
    siniestroId: UUID
    operacionId: UUID
    origen: Literal["automatica", "humana"]
    actor: DecisionActor
    resultado: Literal["aprobado", "rechazado", "inspeccion", "revision", "bloqueado_fraude"]
    estadoAnterior: ClaimState
    estadoNuevo: ClaimState
    transiciones: list[DecisionTransition] = Field(min_length=1)
    reglas: DecisionRules
    motivo: str
    decididaEn: AwareDatetime
    asignacion: Assignment | None


class ClaimDecisions(ReviewModel):
    siniestroId: UUID
    estado: ClaimState
    asignacionActiva: Assignment | None
    decisiones: list[ClaimDecision]


class EvidenceLocation(ReviewModel):
    consentimiento: Literal[True]
    latitud: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitud: float = Field(ge=-180, le=180, allow_inf_nan=False)
    fecha: AwareDatetime


class ReviewEvidence(ReviewModel):
    id: UUID
    siniestroId: UUID
    mimeType: Literal["image/jpeg", "image/png", "application/pdf", "video/mp4"]
    tamanoBytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    autorId: UUID
    creadaEn: AwareDatetime
    fechaCaptura: AwareDatetime | None
    ubicacion: EvidenceLocation | None


class ReviewDetail(ReviewModel):
    siniestro: ClaimDetail
    evidencias: list[ReviewEvidence]


class ClaimReviewError(Exception):
    def __init__(self, status: int = 503, code: str = "servicio_no_disponible") -> None:
        self.status = status
        self.code = code
        super().__init__(code)
