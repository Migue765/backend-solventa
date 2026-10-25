from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class ErrorPerfilamiento(Exception):
    def __init__(self, codigo: str, status: int) -> None:
        super().__init__(codigo)
        self.codigo = codigo
        self.status = status


class Documento(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")


class EventoConsentimiento(Documento):
    evento_id: UUID = Field(alias="eventoId")
    cliente_id: UUID = Field(alias="clienteId")
    consentimiento_id: UUID = Field(alias="consentimientoId")
    version: int = Field(ge=1, strict=True)
    tipo: Literal["ConsentimientoAutorizado", "ConsentimientoRechazado", "ConsentimientoRevocado"]
    estado: Literal["autorizado", "rechazado", "revocado"]
    efectiva_en: AwareDatetime = Field(alias="efectivaEn")
    correlation_id: str = Field(alias="correlationId", min_length=1, max_length=128)

    @model_validator(mode="after")
    def validar_tipo(self) -> "EventoConsentimiento":
        tipos = {
            "autorizado": "ConsentimientoAutorizado",
            "rechazado": "ConsentimientoRechazado",
            "revocado": "ConsentimientoRevocado",
        }
        if tipos[self.estado] != self.tipo:
            raise ValueError("Tipo de evento incompatible con la decision.")
        return self


class Consentimiento(Documento):
    consentimiento_id: UUID = Field(alias="consentimientoId")
    cliente_id: UUID = Field(alias="clienteId")
    version: int = Field(ge=1)
    estado: Literal["autorizado", "rechazado", "revocado", "vencido"]
    finalidad: str
    alcances: tuple[str, ...]
    efectiva_en: AwareDatetime = Field(alias="efectivaEn")
    vigente_hasta: AwareDatetime = Field(alias="vigenteHasta")
    actor_id: UUID | None = Field(default=None, alias="actorId")
    fuentes: tuple[str, ...] = ()
    datos: tuple[str, ...] = ()
    version_terminos: str | None = Field(default=None, alias="versionTerminos")

    def validar_consulta(self, cliente_id: UUID, finalidad: str, ahora: datetime) -> None:
        if (
            self.cliente_id != cliente_id
            or self.estado != "autorizado"
            or self.finalidad != finalidad
            or not self.fuentes
            or not set(self.fuentes).issubset(self.alcances)
            or not set(self.fuentes).issubset({"open_finance", "open_data"})
            or not self.datos
            or self.actor_id != cliente_id
            or self.version_terminos != "1.0"
            or not self.efectiva_en <= ahora < self.vigente_hasta
        ):
            raise ErrorPerfilamiento("consentimiento_no_vigente", 403)

    def validar(self, cliente_id: UUID, ahora: datetime) -> None:
        self.validar_consulta(cliente_id, "cotizacion", ahora)
        if (
            not {"open_finance", "open_data"}.issubset(self.alcances)
            or not {"open_finance", "open_data"}.issubset(self.fuentes)
            or "indicador_riesgo" not in self.datos
        ):
            raise ErrorPerfilamiento("consentimiento_no_vigente", 403)


class Fuente(Documento):
    nombre: Literal["open_finance", "open_data"]
    version: str
    consultada_en: AwareDatetime = Field(alias="consultadaEn")
    vigente_hasta: AwareDatetime = Field(alias="vigenteHasta")
    simulada: bool
    indicador_riesgo: Decimal = Field(alias="indicadorRiesgo", ge=0, le=1)


class Perfil(Documento):
    perfil_id: UUID = Field(alias="perfilId")
    cliente_id: UUID = Field(alias="clienteId")
    referencia_id: UUID = Field(alias="referenciaId")
    version: int = Field(ge=1)
    modelo_version: str = Field(alias="modeloVersion")
    score: Decimal = Field(ge=0, le=1)
    nivel: Literal["A", "B", "C"]
    consentimiento_id: UUID = Field(alias="consentimientoId")
    consentimiento_version: int = Field(alias="consentimientoVersion", ge=1)
    calculado_en: AwareDatetime = Field(alias="calculadoEn")
    vigente_hasta: AwareDatetime = Field(alias="vigenteHasta")
    fuentes: tuple[Fuente, ...]
    fallback_used: bool = Field(default=False, alias="fallbackUsed")

    def validar(self, consentimiento: Consentimiento, ahora: datetime) -> None:
        consentimiento.validar(self.cliente_id, ahora)
        if (
            self.consentimiento_id != consentimiento.consentimiento_id
            or self.consentimiento_version != consentimiento.version
        ):
            raise ErrorPerfilamiento("consentimiento_no_vigente", 403)
        if (
            not self.calculado_en <= ahora < self.vigente_hasta
            or len(self.fuentes) != 2
            or {fuente.nombre for fuente in self.fuentes} != {"open_finance", "open_data"}
            or any(
                not fuente.consultada_en <= ahora < fuente.vigente_hasta for fuente in self.fuentes
            )
        ):
            raise ErrorPerfilamiento("perfil_vencido", 422)


class EvidenciaFuente(Documento):
    fuente: Literal["open_finance", "open_data"]
    version: str
    consultada_en: AwareDatetime = Field(alias="consultadaEn")
    simulada: bool


class ConsultaInformacionCliente(Documento):
    operation_id: UUID = Field(alias="operationId")
    socio_id: str = Field(alias="socioId")
    actor_id: UUID = Field(alias="actorId")
    cliente_id: UUID = Field(alias="clienteId")
    producto_id: str = Field(alias="productoId")
    finalidad: Literal["cotizacion"]
    estado: Literal["completada"] = "completada"
    consentimiento_id: UUID = Field(alias="consentimientoId")
    consentimiento_version: int = Field(alias="consentimientoVersion", ge=1)
    datos: dict[str, str]
    evidencia_fuentes: tuple[EvidenciaFuente, ...] = Field(alias="evidenciaFuentes")
    consultada_en: AwareDatetime = Field(alias="consultadaEn")
