from typing import cast
from uuid import UUID

from flask import Blueprint, Response, current_app, g, jsonify, request
from pydantic import ValidationError

from bff_web.api.security import authenticate, invalid_session_response
from bff_web.application.policy_renewal_ports import PolicyRenewalGateway
from bff_web.domain.errors import InvalidAccessTokenError
from bff_web.domain.policy_renewal import (
    PolicyNotFoundError,
    PolicyNotRenewableError,
    RenewalConflictError,
    RenewalRequest,
    RenewalServiceUnavailableError,
)
from bff_web.problems import problem_response

bp = Blueprint("policy_renewals", __name__, url_prefix="/v1/clientes/me/polizas")

CONFLICT_DETAILS = {
    "condiciones_cambiaron": "Las condiciones de renovacion cambiaron; revisalas nuevamente.",
    "idempotencia_conflictiva": "La clave de la operacion ya se uso con otra solicitud.",
    "renovacion_en_curso": "Ya hay una renovacion en proceso para este periodo.",
}
NOT_RENEWABLE_DETAILS = {
    "producto_no_renovable": "Este producto se contrata por fechas y no admite renovacion.",
    "poliza_programada": "La poliza aun no inicia su vigencia.",
    "poliza_vencida": "La poliza ya vencio; contrata una nueva cobertura.",
    "fuera_de_ventana": "La renovacion se habilita en los 15 dias previos al vencimiento.",
}


@bp.after_request
def private(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    if response.status_code >= 400:
        current_app.logger.warning(
            "renovaciones.rechazo canal=web status=%s correlationId=%s",
            response.status_code,
            g.correlation_id,
        )
    return response


@bp.get("/<policy_id>/renovacion")
def get_eligibility(policy_id: str) -> Response:
    client_id = _client()
    if isinstance(client_id, Response):
        return client_id
    policy = _uuid(policy_id)
    if policy is None or request.args or request.get_data():
        return _invalid("La poliza se identifica con un UUID y no admite parametros.")
    try:
        eligibility = _gateway().get_eligibility(policy, client_id, str(g.correlation_id))
    except PolicyNotFoundError:
        return _not_found()
    except RenewalServiceUnavailableError:
        return _unavailable("No fue posible consultar la renovacion.")
    return jsonify(eligibility.model_dump(mode="json", by_alias=True))


@bp.post("/<policy_id>/renovaciones")
def renew(policy_id: str) -> Response:
    client_id = _client()
    if isinstance(client_id, Response):
        return client_id
    policy = _uuid(policy_id)
    key = _uuid(request.headers.get("Idempotency-Key", ""))
    if policy is None or key is None or request.args:
        return _invalid("Indica la poliza y una Idempotency-Key UUID.")
    try:
        renewal_request = RenewalRequest.model_validate(request.get_json(silent=True))
    except ValidationError:
        return _invalid("Debes aceptar explicitamente las condiciones vigentes.")
    try:
        renewal, status = _gateway().renew(
            policy, client_id, key, renewal_request, str(g.correlation_id)
        )
    except PolicyNotFoundError:
        return _not_found()
    except PolicyNotRenewableError as error:
        return problem_response(
            422,
            "poliza_no_renovable",
            "Poliza no renovable",
            NOT_RENEWABLE_DETAILS[error.reason],
            extensions={"motivo": error.reason},
        )
    except RenewalConflictError as error:
        return problem_response(
            409,
            error.code,
            "Conflicto de renovacion",
            CONFLICT_DETAILS[error.code],
            extensions={"renovacionId": str(error.renewal_id) if error.renewal_id else None},
        )
    except RenewalServiceUnavailableError:
        return _unavailable(
            "No se confirmo la renovacion. Reintenta con la misma clave para conocer el resultado."
        )
    response = jsonify(renewal.model_dump(mode="json", by_alias=True))
    response.status_code = status
    if status == 202:
        response.headers["Retry-After"] = "5"
    return response


def _client() -> UUID | Response:
    try:
        claims = authenticate()
    except InvalidAccessTokenError:
        return invalid_session_response()
    if claims.user.role != "cliente" or claims.scope != "cliente":
        return problem_response(403, "alcance_insuficiente", "Acceso denegado", "Requiere cliente.")
    return claims.user.user_id


def _gateway() -> PolicyRenewalGateway:
    return cast(PolicyRenewalGateway, current_app.extensions["policy_renewal_gateway"])


def _uuid(value: str) -> UUID | None:
    try:
        return UUID(value)
    except ValueError:
        return None


def _invalid(detail: str) -> Response:
    return problem_response(400, "solicitud_invalida", "Solicitud invalida", detail)


def _not_found() -> Response:
    return problem_response(
        404,
        "poliza_no_encontrada",
        "Poliza no encontrada",
        "La poliza no existe o no esta disponible para tu cuenta.",
    )


def _unavailable(detail: str) -> Response:
    response = problem_response(503, "servicio_no_disponible", "Servicio no disponible", detail)
    response.headers["Retry-After"] = "5"
    return response
