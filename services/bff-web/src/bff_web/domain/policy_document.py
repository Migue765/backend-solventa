from dataclasses import dataclass
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    model_validator,
)

from bff_web.domain.policy_renewal import Coverage, Hash, Period, Premium, UtcDatetime


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")


class DocumentConfirmation(_Strict):
    document_id: UUID = Field(alias="documentoId")
    policy_version: Annotated[StrictInt, Field(ge=1)] | None = Field(alias="versionPoliza")
    content_hash: Hash = Field(alias="contenidoHash")
    accepts_simulation: StrictBool = Field(alias="aceptaSimulacion")

    @model_validator(mode="after")
    def explicit_acceptance(self) -> Self:
        if self.accepts_simulation is not True:
            raise ValueError("Se requiere aceptar explicitamente la simulacion.")
        return self


class DocumentMetadata(_Strict):
    document_id: UUID = Field(alias="documentoId")
    policy_id: UUID = Field(alias="polizaId")
    policy_version: Annotated[StrictInt, Field(ge=1)] | None = Field(alias="versionPoliza")
    content_hash: Hash = Field(alias="contenidoHash")
    format: Literal["html"] = Field(alias="formato")
    simulated: Literal[True] = Field(alias="simulada")
    notice: str = Field(alias="aviso", min_length=1)


class DocumentAcceptance(DocumentMetadata):
    acceptance_id: UUID = Field(alias="id")
    actor_id: UUID = Field(alias="actorId")
    accepted_at: UtcDatetime = Field(alias="aceptadaEn")


class DocumentProduct(_Strict):
    type: str = Field(alias="tipo", min_length=1)
    name: str = Field(alias="nombre", min_length=1)


class DocumentCoverage(Coverage):
    code: str = Field(alias="codigo", min_length=1)
    optional: StrictBool = Field(alias="opcional")
    included: StrictBool = Field(alias="incluida")


class DocumentContent(_Strict):
    format_version: Literal[1] = Field(alias="formatoVersion")
    simulated: Literal[True] = Field(alias="simulada")
    notice: str = Field(alias="aviso", min_length=1)
    policy_id: UUID = Field(alias="polizaId")
    client_id: UUID = Field(alias="clienteId")
    subscription_id: UUID = Field(alias="suscripcionId")
    product: DocumentProduct = Field(alias="producto")
    period: Period = Field(alias="vigencia")
    state: Literal["activa", "cancelada"] = Field(alias="estado")
    issued_at: UtcDatetime = Field(alias="emitidaEn")
    issuance_rule: str = Field(alias="reglaEmision", min_length=1)
    version: Annotated[StrictInt, Field(ge=1)] | None
    plan: str | None
    coverages: tuple[DocumentCoverage, ...] = Field(alias="coberturas", min_length=1)
    premium: Premium | None = Field(alias="prima")
    conditions: tuple[str, ...] = Field(alias="condiciones", min_length=1)


class PolicyDocument(DocumentMetadata):
    content: DocumentContent = Field(alias="contenido")
    current_acceptance: DocumentAcceptance | None = Field(alias="aceptacionActual")
    acceptance_history: tuple[DocumentAcceptance, ...] = Field(alias="historialAceptaciones")

    @model_validator(mode="after")
    def consistent_document(self) -> Self:
        if (
            self.content.policy_id != self.policy_id
            or self.content.version != self.policy_version
            or self.content.notice != self.notice
        ):
            raise ValueError("El contenido no corresponde al documento.")
        if self.current_acceptance is not None and (
            self.current_acceptance.document_id != self.document_id
            or self.current_acceptance.content_hash != self.content_hash
            or self.current_acceptance.policy_version != self.policy_version
            or self.current_acceptance not in self.acceptance_history
        ):
            raise ValueError("La aceptacion no corresponde al documento vigente.")
        return self


@dataclass(frozen=True)
class DocumentDownload:
    content: bytes
    content_hash: str


@dataclass(frozen=True)
class PdfDocumentDownload(DocumentDownload):
    document_content_hash: str


class DocumentConflictError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code
