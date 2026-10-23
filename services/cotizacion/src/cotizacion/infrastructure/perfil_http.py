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
    nombre: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    version: str
    consultadaEn: AwareDatetime
    vigenteHasta: AwareDatetime
    simulada: bool
    indicadorRiesgo: Decimal = Field(ge=0, le=1)


class EstadoFuenteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fuente: str
    estado: Literal["disponible", "cache", "fallida"]
    intentos: int = Field(ge=0, le=2)
    motivo: str | None = None


class PerfilResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    perfilId: UUID
    clienteId: UUID
    referenciaId: UUID
    version: int = Field(ge=1)
    modeloVersion: str
    score: Decimal = Field(ge=0, le=1)
    nivel: Literal["A", "B", "C"]
    consentimientoId: UUID
    consentimientoVersion: int = Field(ge=1)
    calculadoEn: AwareDatetime
    vigenteHasta: AwareDatetime
    fuentes: tuple[FuenteResponse, ...]
    fallbackUsed: bool
    estadoFuentes: tuple[EstadoFuenteResponse, ...] = ()


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
    ) -> PerfilRiesgo:
        body: dict[str, str | int] = {
            "clienteId": str(cliente_id),
            "referenciaId": str(referencia_id),
        }
        if consentimiento_id is not None and consentimiento_version is not None:
            body["consentimientoId"] = str(consentimiento_id)
            body["consentimientoVersion"] = consentimiento_version
        perfil = self._consultar(
            "/v1/perfiles",
            cliente_id,
            body,
        )
        if perfil.evidencia is None or perfil.evidencia["referenciaId"] != str(referencia_id):
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
            or not payload.calculadoEn <= ahora < payload.vigenteHasta
            or len(payload.fuentes) != len({fuente.nombre for fuente in payload.fuentes})
            or not {"open_finance", "open_data"}.issubset(
                fuente.nombre for fuente in payload.fuentes
            )
            or any(
                not fuente.consultadaEn <= ahora < fuente.vigenteHasta for fuente in payload.fuentes
            )
            or payload.fallbackUsed
            != any(estado.estado == "cache" for estado in payload.estadoFuentes)
            or (
                payload.estadoFuentes
                and (
                    len(payload.estadoFuentes) != len(payload.fuentes)
                    or {estado.fuente for estado in payload.estadoFuentes}
                    != {fuente.nombre for fuente in payload.fuentes}
                    or any(estado.estado == "fallida" for estado in payload.estadoFuentes)
                )
            )
        ):
            raise ErrorConsultaPerfil("perfilamiento_no_disponible", 503)
        return PerfilRiesgo(
            perfil_id=str(payload.perfilId),
            score=payload.score,
            modelo_version=payload.modeloVersion,
            evidencia=payload.model_dump(mode="json"),
        )
