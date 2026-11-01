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
from bff_web.application.quote_ports import QuoteGateway
from bff_web.domain.auth import AccessClaims
from bff_web.domain.errors import InvalidAccessTokenError
from bff_web.domain.open_finance_exceptions import LinkedException, OpenFinanceExceptionError
from bff_web.domain.quote_errors import QuoteServiceUnavailableError
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


class _ApplyPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")


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
    application_state = request.args.get("estadoAplicacion")
    if application_state is not None and application_state not in {
        "pendiente",
        "bloqueada",
        "completada",
        "detenida",
    }:
        return problem_response(
            400, "solicitud_invalida", "Solicitud invalida", "Estado de aplicacion invalido."
        )
    try:
        result = (
            _gateway().list_pending(partner_id, str(g.correlation_id), application_state)
            if application_state is not None
            else _gateway().list_pending(partner_id, str(g.correlation_id))
        )
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
        result = _apply_pending(result, exception_id, partner_id, claims.user.user_id)
    except OpenFinanceExceptionError as error:
        return _error_response(error)
    return _json_response(result)


@bp.post("/excepciones-open-finance/<uuid:exception_id>/aplicar")
def apply_decision(exception_id: UUID) -> Response:
    claims = _advisor_claims()
    if claims is None:
        return invalid_session_response()
    if not _version_supported():
        return _version_error()
    partner_id = _partner_id(claims)
    if isinstance(partner_id, Response):
        return partner_id
    try:
        _ApplyPayload.model_validate(request.get_json(silent=False))
    except (ValidationError, BadRequest, UnsupportedMediaType):
        return problem_response(
            400, "solicitud_invalida", "Solicitud invalida", "Envia un objeto vacio."
        )
    try:
        result = _gateway().get(exception_id, partner_id, str(g.correlation_id))
        if result.get("proceso") is None:
            raise OpenFinanceExceptionError("excepcion_sin_operacion", 409)
        if result.get("estado") == "pendiente":
            raise OpenFinanceExceptionError("decision_pendiente", 409)
        result = _apply_pending(
            result, exception_id, partner_id, claims.user.user_id, explicit=True
        )
    except OpenFinanceExceptionError as error:
        return _error_response(error)
    return _json_response(result)


def _apply_pending(
    payload: dict[str, object],
    exception_id: UUID,
    partner_id: str,
    actor_id: UUID,
    *,
    explicit: bool = False,
) -> dict[str, object]:
    if payload.get("proceso") is None:
        return payload
    try:
        linked = LinkedException.model_validate(payload)
    except ValidationError as error:
        raise OpenFinanceExceptionError("servicio_no_disponible", 503) from error
    if linked.exception_id != exception_id or linked.partner_id != partner_id:
        raise OpenFinanceExceptionError("servicio_no_disponible", 503)
    if linked.application.state != "pendiente" and not explicit:
        return payload
    impediment = None
    if linked.state == "aprobada" and linked.application.state == "pendiente":
        try:
            products = cast(QuoteGateway, current_app.extensions["quote_gateway"]).list_products()
        except QuoteServiceUnavailableError:
            impediment = "catalogo_no_disponible"
        else:
            if not any(
                item.product_id == linked.process.product_id and item.available for item in products
            ):
                impediment = "producto_no_autorizado"
    return _gateway().apply(exception_id, partner_id, actor_id, str(g.correlation_id), impediment)


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
        "actor_no_autorizado": ("actor_no_autorizado", "Actor no autorizado"),
        "decision_pendiente": ("decision_pendiente", "Decision pendiente"),
        "excepcion_sin_operacion": ("excepcion_sin_operacion", "Excepcion sin operacion vinculada"),
    }
    code, title = public.get(error.code, ("servicio_no_disponible", "Servicio no disponible"))
    status = error.status if error.code in public else 503
    return problem_response(
        status,
        code,
        title,
        "No fue posible consultar o resolver la excepcion.",
    )
