from collections.abc import Callable
from typing import Any, cast
from uuid import UUID

from flask import Blueprint, Response, current_app, g, jsonify, request

from bff_movil.api.security import authenticate, invalid_session_response
from bff_movil.application.policy_renewal_ports import PolicyQueryGateway
from bff_movil.application.ports import SessionStateGateway
from bff_movil.config import Config
from bff_movil.domain.errors import InvalidAccessTokenError
from bff_movil.domain.policy_renewal import PolicyNotFoundError, RenewalServiceUnavailableError
from bff_movil.problems import problem_response

bp = Blueprint("policies", __name__, url_prefix="/movil/v1/polizas")
Fetch = Callable[[PolicyQueryGateway, UUID, str], dict[str, Any]]


@bp.after_request
def private(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@bp.get("")
def list_policies() -> Response:
    return query(
        lambda gateway, client_id, correlation: gateway.list_policies(client_id, correlation)
    )


@bp.get("/<uuid:policy_id>")
def policy_detail(policy_id: UUID) -> Response:
    return query(
        lambda gateway, client_id, correlation: gateway.get_policy(
            policy_id, client_id, correlation
        )
    )


def query(fetch: Fetch) -> Response:
    try:
        claims = authenticate()
    except InvalidAccessTokenError:
        return invalid_session_response()
    if claims.user.role != "cliente" or claims.scope != "cliente":
        return problem_response(403, "alcance_insuficiente", "Acceso denegado", "Requiere cliente.")
    if request.args or request.content_length:
        return problem_response(
            400, "solicitud_invalida", "Solicitud invalida", "La consulta no admite parametros."
        )
    config = cast(Config, current_app.extensions["bff_config"])
    if not config.policies_service_url or not config.internal_api_token:
        return unavailable()
    if claims.session_id is None:
        return invalid_session_response()
    correlation_id = str(g.correlation_id)
    sessions = cast(SessionStateGateway, current_app.extensions["session_state_gateway"])
    if not sessions.session_active(claims.session_id, config.internal_api_token, correlation_id):
        return invalid_session_response()

    gateway = cast(PolicyQueryGateway, current_app.extensions["policy_query_gateway"])
    try:
        document = fetch(gateway, claims.user.user_id, correlation_id)
    except PolicyNotFoundError:
        return problem_response(
            404, "poliza_no_encontrada", "Poliza no encontrada", "La poliza no esta disponible."
        )
    except RenewalServiceUnavailableError:
        current_app.logger.warning(
            "polizas.consulta_no_disponible canal=movil correlationId=%s", correlation_id
        )
        return unavailable()
    return jsonify(document)


def unavailable() -> Response:
    response = problem_response(
        503,
        "servicio_no_disponible",
        "Servicio no disponible",
        "No fue posible consultar tus polizas. Intenta nuevamente.",
    )
    response.headers["Retry-After"] = "5"
    return response
