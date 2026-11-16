from typing import cast
from uuid import UUID

from flask import Blueprint, Response, current_app, jsonify

from identidad.api.advisors import _authorized, _unauthorized
from identidad.application.token_service import TokenService

bp = Blueprint("sessions", __name__, url_prefix="/v1")


@bp.get("/sesiones/<uuid:session_id>")
def session_state(session_id: UUID) -> Response:
    if not _authorized():
        return _unauthorized()
    tokens = cast(TokenService, current_app.extensions["token_service"])
    response = jsonify(sesionId=str(session_id), activa=tokens.is_session_active(session_id))
    response.headers["Cache-Control"] = "no-store"
    return response
