from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class ErrorPerfilamiento(Exception):
    def __init__(self, codigo: str, status: int) -> None:
        super().__init__(codigo)
        self.codigo = codigo
        self.status = status


class Documento(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")


class Consentimiento(Documento):
    consentimiento_id: UUID = Field(alias="consentimientoId")
    cliente_id: UUID = Field(alias="clienteId")
    version: int = Field(ge=1)
    estado: Literal["autorizado", "revocado"]
    finalidad: str
    alcances: tuple[str, ...]
    efectiva_en: AwareDatetime = Field(alias="efectivaEn")
    vigente_hasta: AwareDatetime = Field(alias="vigenteHasta")

    def validar(self, cliente_id: UUID, ahora: datetime) -> None:
        if (
            self.cliente_id != cliente_id
            or self.estado != "autorizado"
            or self.finalidad != "cotizacion"
            or not {"open_finance", "open_data"}.issubset(self.alcances)
            or not self.efectiva_en <= ahora < self.vigente_hasta
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
