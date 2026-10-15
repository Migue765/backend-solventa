from typing import cast

from flask import Blueprint, Response, current_app, jsonify
from pydantic import ValidationError

from identidad.api.request_body import read_body
from identidad.api.schemas import RevokeRequest, TokenRequest, tokens_to_json
from identidad.api.users import too_many_attempts
from identidad.application.token_service import TokenService
from identidad.domain.errors import (
    InvalidCredentialsError,
    InvalidRefreshTokenError,
    TooManyAttemptsError,
)
from identidad.problems import problem_response

bp = Blueprint("oauth", __name__, url_prefix="/oauth")


@bp.post("/token")
def token() -> Response:
    try:
        payload = TokenRequest.model_validate(read_body(allow_form=True))
    except ValidationError:
        return _invalid_request()

    service = _token_service()
    try:
        if payload.grant_type == "password":
            issued = service.password_grant(
                str(payload.username), cast(str, payload.password), payload.client_id
            )
        else:
            issued = service.refresh_grant(cast(str, payload.refresh_token), payload.client_id)
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
