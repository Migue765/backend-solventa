import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import UUID

from flask import g, has_request_context
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from cotizacion.domain.errors import ErrorConsultaPerfil
from cotizacion.domain.tarifa import PerfilRiesgo


class FuenteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nombre: Literal["open_finance", "open_data"]
    version: str
    consultadaEn: AwareDatetime
    vigenteHasta: AwareDatetime
    simulada: bool
    indicadorRiesgo: Decimal = Field(ge=0, le=1)
    variablesDisponibles: tuple[str, ...]


class VariableResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nombre: Literal["indicador_riesgo"]
    fuente: Literal["open_finance", "open_data"]
    valorNormalizado: Decimal = Field(ge=0, le=1)
    autorizadaPor: UUID


class VariableExcluidaResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nombre: str
    fuente: Literal["open_finance", "open_data"]
    motivo: Literal["sin_consentimiento"]


class ReglaRiesgoResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reglaId: Literal["riesgo-maximo-fuentes"]
    version: Literal["1.0", "legacy"]
    modeloVersion: Literal["perfil-riesgo-1", "perfil-mock-1"]
    expresion: Literal["max(indicador_riesgo_por_fuente)"]


class DegradacionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fuente: Literal["open_finance", "open_data"]
    motivo: Literal["fuente_no_disponible", "fuente_vencida"]
    politica: Literal["cache", "default"]
    versionPolitica: Literal["degradacion-1"]


class PerfilResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    perfilId: UUID
    clienteId: UUID
    referenciaId: UUID
    operationId: UUID
    creditoId: str
    version: int = Field(ge=1)
    modeloVersion: str
    score: Decimal = Field(ge=0, le=1)
    nivel: Literal["A", "B", "C"]
    consentimientoId: UUID
    consentimientoVersion: int = Field(ge=1)
    calculadoEn: AwareDatetime
    vigenteHasta: AwareDatetime
    fuentes: tuple[FuenteResponse, ...]
    variables: tuple[VariableResponse, ...]
    variablesExcluidas: tuple[VariableExcluidaResponse, ...]
    regla: ReglaRiesgoResponse
    degradaciones: tuple[DegradacionResponse, ...]
    hashDecision: str = Field(pattern=r"^[0-9a-f]{64}$")
    fallbackUsed: bool


class ProveedorPerfilHttp:
    def __init__(self, base_url: str, token: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token

    def generar(
        self,
        cliente_id: UUID,
        referencia_id: UUID,
        consentimiento_id: UUID | None = None,
        consentimiento_version: int | None = None,
        operation_id: UUID | None = None,
        credito_id: str | None = None,
    ) -> PerfilRiesgo:
        body: dict[str, str | int] = {
            "clienteId": str(cliente_id),
            "referenciaId": str(referencia_id),
        }
        if consentimiento_id is not None and consentimiento_version is not None:
            body["consentimientoId"] = str(consentimiento_id)
            body["consentimientoVersion"] = consentimiento_version
        if operation_id is not None:
            body["operationId"] = str(operation_id)
        if credito_id is not None:
            body["creditoId"] = credito_id
        perfil = self._consultar(
            "/v1/perfiles",
            cliente_id,
            body,
        )
        if perfil.evidencia is None or perfil.evidencia["referenciaId"] != str(referencia_id):
            raise ErrorConsultaPerfil("perfilamiento_no_disponible", 503)
        if operation_id is not None and perfil.evidencia["operationId"] != str(operation_id):
            raise ErrorConsultaPerfil("perfilamiento_no_disponible", 503)
        if credito_id is not None and perfil.evidencia["creditoId"] != credito_id:
            raise ErrorConsultaPerfil("perfilamiento_no_disponible", 503)
        if consentimiento_id is not None and (
            perfil.evidencia["consentimientoId"] != str(consentimiento_id)
            or perfil.evidencia["consentimientoVersion"] != consentimiento_version
        ):
            raise ErrorConsultaPerfil("consentimiento_no_vigente", 403)
        return perfil

    def obtener(self, perfil_id: str, cliente_id: UUID) -> PerfilRiesgo:
        try:
            identificador = UUID(perfil_id)
        except ValueError as error:
            raise ErrorConsultaPerfil("perfil_no_encontrado", 404) from error
        query = urlencode({"clienteId": str(cliente_id)})
        perfil = self._consultar(f"/v1/perfiles/{identificador}?{query}", cliente_id)
        if perfil.perfil_id != str(identificador):
            raise ErrorConsultaPerfil("perfilamiento_no_disponible", 503)
        return perfil

    def _consultar(
        self, path: str, cliente_id: UUID, body: dict[str, str | int] | None = None
    ) -> PerfilRiesgo:
        if not self._token.strip():
            raise ErrorConsultaPerfil("perfilamiento_no_disponible", 503)
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if has_request_context():
            headers["X-Correlation-ID"] = str(g.correlation_id)
        request = Request(
            self._base_url + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers=headers,
        )
        try:
            with urlopen(request, timeout=0.7) as response:
                payload = PerfilResponse.model_validate(json.load(response))
        except HTTPError as error:
            try:
                problem: Any = json.load(error)
            except (ValueError, OSError):
                raise ErrorConsultaPerfil("perfilamiento_no_disponible", 503) from error
            code = problem.get("error") if isinstance(problem, dict) else None
            allowed = {
                "consentimiento_ausente": 403,
                "consentimiento_no_vigente": 403,
                "perfil_vencido": 422,
                "perfil_no_encontrado": 404,
                "conflicto_version": 409,
            }
            if isinstance(code, str) and allowed.get(code) == error.code:
                raise ErrorConsultaPerfil(code, error.code) from error
            raise ErrorConsultaPerfil("perfilamiento_no_disponible", 503) from error
        except (URLError, OSError, ValueError, ValidationError) as error:
            raise ErrorConsultaPerfil("perfilamiento_no_disponible", 503) from error
        ahora = datetime.now(UTC)
        if (
            payload.clienteId != cliente_id
            or payload.calculadoEn > ahora
            or len(payload.fuentes) != 2
            or {fuente.nombre for fuente in payload.fuentes} != {"open_finance", "open_data"}
            or any(fuente.consultadaEn > ahora for fuente in payload.fuentes)
        ):
            raise ErrorConsultaPerfil("perfilamiento_no_disponible", 503)
        if payload.vigenteHasta <= ahora:
            raise ErrorConsultaPerfil("perfil_vencido", 422)
        if any(fuente.vigenteHasta <= ahora for fuente in payload.fuentes):
            raise ErrorConsultaPerfil("fuente_vencida", 422)
        return PerfilRiesgo(
            perfil_id=str(payload.perfilId),
            score=payload.score,
            modelo_version=payload.modeloVersion,
            evidencia=payload.model_dump(mode="json"),
        )
