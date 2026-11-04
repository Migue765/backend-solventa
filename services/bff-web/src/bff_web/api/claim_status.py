from typing import cast
from uuid import UUID

from flask import Blueprint, Response, current_app, g, jsonify, request

from bff_web.api.claims import private
from bff_web.api.security import authenticate, invalid_session_response
from bff_web.application.claim_ports import ClaimGateway
from bff_web.domain.claims import ClaimNotFoundError, ClaimServiceUnavailableError
from bff_web.domain.errors import InvalidAccessTokenError
from bff_web.problems import problem_response

bp = Blueprint("claim_status", __name__, url_prefix="/v1/clientes/me/siniestros")
bp.after_request(private)


@bp.get("/<claim_id>")
def get_status(claim_id: str) -> Response:
    try:
        claims = authenticate()
    except InvalidAccessTokenError:
        return invalid_session_response()
    if claims.user.role != "cliente" or claims.scope != "cliente":
        return problem_response(403, "alcance_insuficiente", "Acceso denegado", "Requiere cliente.")
    if request.args or request.get_data():
        return _invalid()
    try:
        requested = UUID(claim_id)
    except ValueError:
        return _invalid()
    gateway = cast(ClaimGateway, current_app.extensions["claim_gateway"])
    try:
        status = gateway.get_status(requested, claims.user.user_id, str(g.correlation_id))
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
    return jsonify(status.model_dump(mode="json", by_alias=True))


def _invalid() -> Response:
    return problem_response(
        400,
        "solicitud_invalida",
        "Solicitud invalida",
        "El siniestro se identifica con un UUID y el cliente con la sesion autenticada.",
    )
