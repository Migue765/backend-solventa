from typing import cast
from uuid import UUID

from flask import Blueprint, Response, current_app, g, jsonify, request

from bff_web.api.security import authenticate, invalid_session_response
from bff_web.application.claim_ports import ClaimGateway
from bff_web.domain.claims import ClaimNotFoundError, ClaimServiceUnavailableError
from bff_web.domain.errors import InvalidAccessTokenError
from bff_web.problems import problem_response

bp = Blueprint("claims", __name__, url_prefix="/v1/clientes/me/siniestros")


@bp.get("/<claim_id>")
def get_status(claim_id: str) -> Response:
    try:
        user = authenticate().user
    except InvalidAccessTokenError:
        return invalid_session_response()
    if request.args or request.get_data():
        return _invalid()
    try:
        requested = UUID(claim_id)
    except ValueError:
        return _invalid()
    gateway = cast(ClaimGateway, current_app.extensions["claim_gateway"])
    try:
        status = gateway.get_status(requested, user.user_id, str(g.correlation_id))
    except ClaimNotFoundError:
        return problem_response(
            404,
            "siniestro_no_encontrado",
            "Siniestro no encontrado",
            "El siniestro no existe o no esta disponible para tu cuenta.",
        )
    except ClaimServiceUnavailableError:
        response = problem_response(
            503,
            "servicio_no_disponible",
            "Servicio no disponible",
            "No fue posible consultar el estado del siniestro. Intentalo nuevamente.",
        )
        response.headers["Retry-After"] = "5"
        return response
    response = jsonify(status.model_dump(mode="json", by_alias=True))
    response.headers["Cache-Control"] = "no-store"
    return response


def _invalid() -> Response:
    return problem_response(
        400,
        "solicitud_invalida",
        "Solicitud invalida",
        "El siniestro se identifica con un UUID y el cliente con la sesion autenticada.",
    )
