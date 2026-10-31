from typing import Literal, cast
from uuid import UUID

from flask import Blueprint, Response, current_app, g, jsonify, request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from werkzeug.exceptions import BadRequest, UnsupportedMediaType

from bff_web.api.security import (
    audit_rejected_response,
    authenticate,
    bearer_token,
    invalid_session_response,
)
from bff_web.application.open_finance_exception_ports import OpenFinanceExceptionGateway
from bff_web.domain.auth import AccessClaims
from bff_web.domain.errors import InvalidAccessTokenError
from bff_web.domain.open_finance_exceptions import OpenFinanceExceptionError
from bff_web.problems import problem_response

bp = Blueprint("open_finance_exceptions", __name__, url_prefix="/v1/asesor")
bp.after_request(audit_rejected_response)


class _ResolutionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["aprobar", "rechazar"]
    motivo: str = Field(min_length=3, max_length=500)

    @field_validator("motivo")
    @classmethod
    def motivo_no_vacio(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 3:
            raise ValueError("El motivo debe incluir al menos tres caracteres.")
        return normalized


@bp.get("/excepciones-open-finance")
def list_pending() -> Response:
    claims = _advisor_claims()
    if claims is None:
        return invalid_session_response()
    if not _version_supported():
        return _version_error()
    partner_id = _partner_id(claims)
    if isinstance(partner_id, Response):
        return partner_id
    try:
        result = _gateway().list_pending(partner_id, str(g.correlation_id))
    except OpenFinanceExceptionError as error:
        return _error_response(error)
    return _json_response(result)


@bp.get("/excepciones-open-finance/<uuid:exception_id>")
def get_exception(exception_id: UUID) -> Response:
    claims = _advisor_claims()
    if claims is None:
        return invalid_session_response()
    if not _version_supported():
        return _version_error()
    partner_id = _partner_id(claims)
    if isinstance(partner_id, Response):
        return partner_id
    try:
        result = _gateway().get(exception_id, partner_id, str(g.correlation_id))
    except OpenFinanceExceptionError as error:
        return _error_response(error)
    return _json_response(result)


@bp.post("/excepciones-open-finance/<uuid:exception_id>/resolver")
def resolve(exception_id: UUID) -> Response:
    claims = _advisor_claims()
    if claims is None:
        return invalid_session_response()
    if not _version_supported():
        return _version_error()
    partner_id = _partner_id(claims)
    if isinstance(partner_id, Response):
        return partner_id
    try:
        payload = _ResolutionPayload.model_validate(request.get_json(silent=False))
    except (ValidationError, BadRequest, UnsupportedMediaType):
        return problem_response(
            400,
            "solicitud_invalida",
            "Solicitud invalida",
            "La decision debe ser aprobar o rechazar e incluir un motivo de 3 a 500 caracteres.",
        )
    try:
        result = _gateway().resolve(
            exception_id,
            partner_id,
            claims.user.user_id,
            payload.decision,
            payload.motivo,
            str(g.correlation_id),
        )
    except OpenFinanceExceptionError as error:
        return _error_response(error)
    return _json_response(result)


def _advisor_claims() -> AccessClaims | None:
    try:
        claims = authenticate()
    except InvalidAccessTokenError:
        g.audit_rejection_reason = (
            "credencial_ausente" if bearer_token() is None else "credencial_invalida"
        )
        return None
    if claims.scope != "asesor" or claims.user.role != "asesor":
        g.audit_rejection_reason = "alcance_insuficiente"
        return None
    g.audit_actor_id = str(claims.user.user_id)
    return claims


def _partner_id(claims: AccessClaims) -> str | Response:
    partner_id = request.args.get("socioId", "")
    if not partner_id or len(partner_id) > 50:
        return problem_response(
            400, "solicitud_invalida", "Solicitud invalida", "Indica un socio autorizado."
        )
    if partner_id not in claims.user.partner_ids:
        g.audit_rejection_reason = "socio_no_autorizado"
        return problem_response(
            403, "socio_no_autorizado", "Acceso denegado", "La cuenta no pertenece al socio."
        )
    g.audit_partner_id = partner_id
    return partner_id


def _gateway() -> OpenFinanceExceptionGateway:
    return cast(
        OpenFinanceExceptionGateway,
        current_app.extensions["open_finance_exception_gateway"],
    )


def _json_response(payload: dict[str, object]) -> Response:
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    response.headers["API-Version"] = "1"
    return response


def _version_supported() -> bool:
    return request.headers.get("API-Version", "1") == "1"


def _version_error() -> Response:
    return problem_response(
        400, "version_no_soportada", "Version no soportada", "Usa la version 1 de la API."
    )


def _error_response(error: OpenFinanceExceptionError) -> Response:
    public = {
        "consentimiento_ausente": ("consentimiento_ausente", "Consentimiento no encontrado"),
        "consentimiento_no_vigente": ("consentimiento_no_vigente", "Consentimiento no vigente"),
        "excepcion_no_encontrada": ("excepcion_no_encontrada", "Excepcion no encontrada"),
        "excepcion_ya_resuelta": ("excepcion_ya_resuelta", "Excepcion ya resuelta"),
        "conflicto_idempotencia": ("conflicto_idempotencia", "Conflicto de idempotencia"),
        "solicitud_invalida": ("solicitud_invalida", "Solicitud invalida"),
    }
    code, title = public.get(error.code, ("servicio_no_disponible", "Servicio no disponible"))
    status = error.status if error.code in public else 503
    return problem_response(
        status,
        code,
        title,
        "No fue posible consultar o resolver la excepcion.",
    )
