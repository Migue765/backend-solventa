from flask import Blueprint, Response, jsonify, request
from pydantic import ValidationError

from bff_web.api.identity import (
    correlation_id,
    identity_gateway,
    invalid_request_response,
    rejection_response,
)
from bff_web.api.schemas import RegisterRequest, created_user_to_json
from bff_web.domain.errors import IdentityRejectedError

bp = Blueprint("users", __name__, url_prefix="/v1/usuarios")


@bp.post("")
def register() -> Response:
    try:
        payload = RegisterRequest.model_validate(request.get_json(silent=False))
    except (ValidationError, TypeError):
        return invalid_request_response()

    try:
        user = identity_gateway().register(
            payload.nombre, str(payload.correo), payload.contrasena, correlation_id()
        )
    except IdentityRejectedError as error:
        return rejection_response(error)

    response = jsonify(created_user_to_json(user))
    response.status_code = 201
    response.headers["Cache-Control"] = "no-store"
    return response
