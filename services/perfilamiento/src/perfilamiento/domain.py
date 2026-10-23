from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class ErrorPerfilamiento(Exception):
    def __init__(
        self, codigo: str, status: int, fuentes: list[dict[str, object]] | None = None
    ) -> None:
        super().__init__(codigo)
        self.codigo = codigo
        self.status = status
        self.fuentes = fuentes


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

    def validar(
        self,
        cliente_id: UUID,
        ahora: datetime,
        fuentes: tuple[str, ...] = ("open_finance", "open_data"),
        finalidad: str = "cotizacion",
        datos: tuple[str, ...] = ("indicador_riesgo",),
    ) -> None:
        if (
            self.cliente_id != cliente_id
            or self.estado != "autorizado"
            or self.finalidad != finalidad
            or not set(fuentes).issubset(self.alcances)
            or not set(fuentes).issubset(self.fuentes)
            or not set(datos).issubset(self.datos)
            or self.actor_id != cliente_id
            or self.version_terminos != "1.0"
            or not self.efectiva_en <= ahora < self.vigente_hasta
        ):
            raise ErrorPerfilamiento("consentimiento_no_vigente", 403)


class Fuente(Documento):
    nombre: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    version: str
    consultada_en: AwareDatetime = Field(alias="consultadaEn")
    vigente_hasta: AwareDatetime = Field(alias="vigenteHasta")
    simulada: bool
    indicador_riesgo: Decimal = Field(alias="indicadorRiesgo", ge=0, le=1)


class EstadoFuente(Documento):
    fuente: str
    estado: Literal["disponible", "cache", "fallida"]
    intentos: int = Field(ge=0, le=2)
    motivo: str | None = None


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
    estado_fuentes: tuple[EstadoFuente, ...] = Field(default=(), alias="estadoFuentes")

    def validar(self, consentimiento: Consentimiento, ahora: datetime) -> None:
        nombres = tuple(fuente.nombre for fuente in self.fuentes)
        consentimiento.validar(self.cliente_id, ahora, nombres)
        if (
            self.consentimiento_id != consentimiento.consentimiento_id
            or self.consentimiento_version != consentimiento.version
        ):
            raise ErrorPerfilamiento("consentimiento_no_vigente", 403)
        if (
            not self.calculado_en <= ahora < self.vigente_hasta
            or len(nombres) != len(set(nombres))
            or not {"open_finance", "open_data"}.issubset(nombres)
            or any(
                not fuente.consultada_en <= ahora < fuente.vigente_hasta for fuente in self.fuentes
            )
        ):
            raise ErrorPerfilamiento("perfil_vencido", 422)
        if self.fallback_used != any(
            estado.estado == "cache" for estado in self.estado_fuentes
        ) or (
            self.estado_fuentes
            and (
                len(self.estado_fuentes) != len(nombres)
                or {estado.fuente for estado in self.estado_fuentes} != set(nombres)
                or any(estado.estado == "fallida" for estado in self.estado_fuentes)
            )
        ):
            raise ErrorPerfilamiento("perfil_no_integro", 503)
