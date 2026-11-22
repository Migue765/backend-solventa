from typing import cast

from flask import Blueprint, Response, current_app, jsonify, request
from pydantic import BaseModel, ConfigDict, EmailStr, Field, ValidationError
from werkzeug.exceptions import RequestEntityTooLarge

from bff_web.api.identity import (
    correlation_id,
    identity_gateway,
    invalid_request_response,
    rejection_response,
)
from bff_web.config import Config
from bff_web.domain.errors import IdentityRejectedError, IdentityUnavailableError

bp = Blueprint("password_recovery", __name__, url_prefix="/v1/recuperacion-contrasena")


class RecoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    correo: EmailStr = Field(max_length=254)


class RecoveryConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(pattern=r"^[A-Za-z0-9_-]{64}$", strict=True)
    credencialCifrada: str = Field(min_length=1, max_length=8192, strict=True)


@bp.post("")
def request_recovery() -> Response:
    request.max_content_length = 2048
    try:
        payload = RecoveryRequest.model_validate(request.get_json(silent=False))
    except (ValidationError, TypeError, RequestEntityTooLarge):
        return invalid_request_response()
    try:
        message = identity_gateway().request_password_recovery(
            str(payload.correo), correlation_id(), request.remote_addr
        )
    except IdentityRejectedError as error:
        return rejection_response(error)
    response = jsonify({"mensaje": message})
    response.status_code = 202
    return response


@bp.get("/desafio")
def challenge() -> Response:
    _require_encryption()
    try:
        return jsonify(identity_gateway().recovery_challenge(correlation_id(), request.remote_addr))
    except IdentityRejectedError as error:
        return rejection_response(error)


@bp.post("/confirmar")
def confirm() -> Response:
    request.max_content_length = 16384
    _require_encryption()
    try:
        payload = RecoveryConfirmation.model_validate(request.get_json(silent=False))
    except (ValidationError, TypeError, RequestEntityTooLarge):
        return invalid_request_response()
    try:
        identity_gateway().confirm_password_recovery(
            payload.token, payload.credencialCifrada, correlation_id(), request.remote_addr
        )
    except IdentityRejectedError as error:
        return rejection_response(error)
    return Response(status=204)


def _require_encryption() -> None:
    if not cast(Config, current_app.extensions["bff_config"]).encrypted_login_enabled:
        raise IdentityUnavailableError()
