from typing import cast

from flask import Blueprint, Response, current_app, jsonify, request
from pydantic import ValidationError
from werkzeug.exceptions import RequestEntityTooLarge

from identidad.api.biometrics import biometric_token
from identidad.api.request_body import read_body
from identidad.api.schemas import EncryptedTokenRequest, RevokeRequest, TokenRequest, tokens_to_json
from identidad.api.users import too_many_attempts
from identidad.application.token_service import TokenService
from identidad.config import Config
from identidad.domain.errors import (
    InvalidCredentialsError,
    InvalidRefreshTokenError,
    TooManyAttemptsError,
)
from identidad.infrastructure.encrypted_login import EncryptedLogin, InvalidEnvelopeError
from identidad.problems import problem_response

bp = Blueprint("oauth", __name__, url_prefix="/oauth")


@bp.get("/login-challenge")
def login_challenge() -> Response:
    if not _config().encrypted_login_enabled:
        return _unavailable()
    response = jsonify(_encrypted_login().challenge())
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


@bp.post("/token")
def token() -> Response:
    request.max_content_length = 16384
    payload: TokenRequest | EncryptedTokenRequest
    try:
        body = read_body(allow_form=True)
        if body.get("grant_type") == "biometric_signature":
            return biometric_token(body)
        if isinstance(body, dict) and body.get("grant_type") == "encrypted_password":
            if not _config().encrypted_login_enabled:
                return _unavailable()
            payload = EncryptedTokenRequest.model_validate(body)
        else:
            payload = TokenRequest.model_validate(body)
    except (ValidationError, RequestEntityTooLarge):
        return _invalid_request()

    if (
        _config().encrypted_login_enabled
        and payload.grant_type == "password"
        and payload.client_id == "web"
    ):
        return _invalid_request()

    service = _token_service()
    try:
        if isinstance(payload, EncryptedTokenRequest):
            password = _encrypted_login().decrypt_and_consume(
                str(payload.username), payload.credencialCifrada
            )
            issued = service.password_grant(str(payload.username), password, "web")
        elif payload.grant_type == "password":
            issued = service.password_grant(
                str(payload.username), cast(str, payload.password), payload.client_id
            )
        else:
            issued = service.refresh_grant(cast(str, payload.refresh_token), payload.client_id)
    except InvalidEnvelopeError:
        return _invalid_request()
    except InvalidCredentialsError:
        return problem_response(
            status=401,
            code="credenciales_invalidas",
            title="No fue posible iniciar sesión",
            detail="Las credenciales no son válidas.",
        )
    except InvalidRefreshTokenError:
        return problem_response(
            status=401,
            code="refresh_invalido",
            title="Sesión no válida",
            detail="Inicia sesión nuevamente.",
        )
    except TooManyAttemptsError as error:
        return too_many_attempts(error)

    response = jsonify(tokens_to_json(issued))
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


def _config() -> Config:
    return cast(Config, current_app.extensions["identidad_config"])


def _encrypted_login() -> EncryptedLogin:
    return cast(EncryptedLogin, current_app.extensions["encrypted_login"])


def _unavailable() -> Response:
    response = problem_response(
        status=503,
        code="servicio_no_disponible",
        title="Servicio no disponible",
        detail="El inicio de sesión cifrado no está disponible.",
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.post("/revoke")
def revoke() -> Response:
    try:
        payload = RevokeRequest.model_validate(read_body(allow_form=True))
    except ValidationError:
        return _invalid_request()

    _token_service().revoke(payload.refresh_token)
    response = Response(status=204)
    response.headers["Cache-Control"] = "no-store"
    return response


def _invalid_request() -> Response:
    return problem_response(
        status=400,
        code="solicitud_invalida",
        title="Solicitud inválida",
        detail="Revisa los datos enviados.",
    )


def _token_service() -> TokenService:
    return cast(TokenService, current_app.extensions["token_service"])
