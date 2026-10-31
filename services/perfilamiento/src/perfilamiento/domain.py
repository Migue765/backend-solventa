import hashlib
import json
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


class VariableRiesgo(Documento):
    nombre: Literal["indicador_riesgo"]
    fuente: Literal["open_finance", "open_data"]
    valor_normalizado: Decimal = Field(alias="valorNormalizado", ge=0, le=1)
    autorizada_por: UUID = Field(alias="autorizadaPor")


class VariableExcluida(Documento):
    nombre: str
    fuente: Literal["open_finance", "open_data"]
    motivo: Literal["sin_consentimiento"]


class ReglaRiesgo(Documento):
    regla_id: Literal["riesgo-maximo-fuentes"] = Field(alias="reglaId")
    version: Literal["1.0", "legacy"]
    modelo_version: Literal["perfil-riesgo-1", "perfil-mock-1"] = Field(alias="modeloVersion")
    expresion: Literal["max(indicador_riesgo_por_fuente)"]


class Degradacion(Documento):
    fuente: Literal["open_finance", "open_data"]
    motivo: Literal["fuente_no_disponible", "fuente_vencida"]
    politica: Literal["cache", "default"]
    version_politica: Literal["degradacion-1"] = Field(alias="versionPolitica")


class Perfil(Documento):
    perfil_id: UUID = Field(alias="perfilId")
    cliente_id: UUID = Field(alias="clienteId")
    referencia_id: UUID = Field(alias="referenciaId")
    operation_id: UUID = Field(alias="operationId")
    credito_id: str = Field(alias="creditoId", min_length=1, max_length=64)
    version: int = Field(ge=1)
    modelo_version: str = Field(alias="modeloVersion")
    score: Decimal = Field(ge=0, le=1)
    nivel: Literal["A", "B", "C"]
    consentimiento_id: UUID = Field(alias="consentimientoId")
    consentimiento_version: int = Field(alias="consentimientoVersion", ge=1)
    calculado_en: AwareDatetime = Field(alias="calculadoEn")
    vigente_hasta: AwareDatetime = Field(alias="vigenteHasta")
    fuentes: tuple[Fuente, ...]
    variables: tuple[VariableRiesgo, ...]
    variables_excluidas: tuple[VariableExcluida, ...] = Field(alias="variablesExcluidas")
    regla: ReglaRiesgo
    degradaciones: tuple[Degradacion, ...] = ()
    hash_decision: str = Field(alias="hashDecision", pattern=r"^[0-9a-f]{64}$")
    fallback_used: bool = Field(default=False, alias="fallbackUsed")

    @model_validator(mode="before")
    @classmethod
    def completar_evidencia_legada(cls, value: object) -> object:
        if not isinstance(value, dict) or "operationId" in value or "operation_id" in value:
            return value
        contenido = dict(value)
        referencia_id = contenido.get("referenciaId")
        consentimiento_id = contenido.get("consentimientoId")
        fuentes = contenido.get("fuentes", [])
        variables = [
            {
                "nombre": "indicador_riesgo",
                "fuente": fuente["nombre"],
                "valorNormalizado": fuente["indicadorRiesgo"],
                "autorizadaPor": consentimiento_id,
            }
            for fuente in fuentes
            if isinstance(fuente, dict)
        ]
        regla = {
            "reglaId": "riesgo-maximo-fuentes",
            "version": "legacy",
            "modeloVersion": contenido.get("modeloVersion", "perfil-mock-1"),
            "expresion": "max(indicador_riesgo_por_fuente)",
        }
        evidencia = {
            "clienteId": str(contenido.get("clienteId")),
            "creditoId": str(referencia_id),
            "operationId": str(referencia_id),
            "consentimientoId": str(consentimiento_id),
            "consentimientoVersion": contenido.get("consentimientoVersion"),
            "fuentes": fuentes,
            "variables": variables,
            "variablesExcluidas": [],
            "regla": regla,
            "degradaciones": [],
            "score": str(contenido.get("score")),
        }
        contenido.update(
            operationId=referencia_id,
            creditoId=str(referencia_id),
            variables=variables,
            variablesExcluidas=[],
            regla=regla,
            degradaciones=[],
            hashDecision=hashlib.sha256(
                json.dumps(evidencia, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        )
        return contenido

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
