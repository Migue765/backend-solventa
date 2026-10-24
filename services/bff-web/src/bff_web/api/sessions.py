from typing import cast

from flask import Blueprint, Response, current_app, jsonify, request
from pydantic import ValidationError
from werkzeug.exceptions import RequestEntityTooLarge

from bff_web.api.identity import (
    clock,
    correlation_id,
    identity_gateway,
    invalid_request_response,
    rejection_response,
)
from bff_web.api.schemas import (
    EncryptedLoginRequest,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    claims_to_json,
    token_set_to_json,
)
from bff_web.api.security import authenticate, invalid_session_response
from bff_web.config import Config
from bff_web.domain.auth import TokenSet
from bff_web.domain.errors import (
    IdentityRejectedError,
    IdentityUnavailableError,
    InvalidAccessTokenError,
)

bp = Blueprint("sessions", __name__, url_prefix="/v1/sesiones")


@bp.get("/desafio")
def challenge() -> Response:
    if not _encrypted_enabled():
        raise IdentityUnavailableError()
    try:
        payload = identity_gateway().login_challenge(correlation_id())
    except IdentityRejectedError as error:
        return rejection_response(error)
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


def _encrypted_enabled() -> bool:
    return cast(Config, current_app.extensions["bff_config"]).encrypted_login_enabled


@bp.post("")
def login() -> Response:
    request.max_content_length = 16384
    if _encrypted_enabled():
        try:
            encrypted = EncryptedLoginRequest.model_validate(request.get_json(silent=False))
        except (ValidationError, TypeError, RequestEntityTooLarge):
            return invalid_request_response()
        try:
            tokens = identity_gateway().encrypted_login(
                str(encrypted.correo), encrypted.credencialCifrada, correlation_id()
            )
        except IdentityRejectedError as error:
            return rejection_response(error)
        return _tokens_response(tokens)
    try:
        payload = LoginRequest.model_validate(request.get_json(silent=False))
    except (ValidationError, TypeError, RequestEntityTooLarge):
        return invalid_request_response()

    try:
        tokens = identity_gateway().login(str(payload.correo), payload.contrasena, correlation_id())
    except IdentityRejectedError as error:
        return rejection_response(error)
    return _tokens_response(tokens)


@bp.post("/renovar")
def renew() -> Response:
    try:
        payload = RefreshRequest.model_validate(request.get_json(silent=False))
    except (ValidationError, TypeError):
        return invalid_request_response()

    try:
        tokens = identity_gateway().refresh(payload.refresh_token, correlation_id())
    except IdentityRejectedError as error:
        return rejection_response(error)
    return _tokens_response(tokens)


@bp.get("/actual")
def current_session() -> Response:
    try:
        claims = authenticate()
    except InvalidAccessTokenError:
        return invalid_session_response()

    response = jsonify(claims_to_json(claims))
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.delete("/actual")
def logout() -> Response:
    try:
        authenticate()
    except InvalidAccessTokenError:
        return invalid_session_response()

    body = request.get_json(silent=True)
    try:
        payload = LogoutRequest.model_validate(body if body is not None else {})
    except ValidationError:
        return invalid_request_response()

    if payload.refresh_token:
        try:
            identity_gateway().revoke(payload.refresh_token, correlation_id())
        except IdentityRejectedError as error:
            return rejection_response(error)

    response = Response(status=204)
    response.headers["Cache-Control"] = "no-store"
    return response


def _tokens_response(tokens: TokenSet) -> Response:
    response = jsonify(token_set_to_json(tokens, clock()()))
    response.headers["Cache-Control"] = "no-store"
    return response
