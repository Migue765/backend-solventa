from typing import cast

from flask import Response, current_app, request

from bff_movil.application.ports import TokenVerifier
from bff_movil.domain.auth import AccessClaims
from bff_movil.domain.errors import InvalidAccessTokenError
from bff_movil.problems import problem_response

BEARER_PREFIX = "bearer "


def authenticate() -> AccessClaims:
    token = bearer_token()
    if token is None:
        raise InvalidAccessTokenError()
    verifier = cast(TokenVerifier, current_app.extensions["token_verifier"])
    return verifier.verify(token)


def bearer_token() -> str | None:
    header = request.headers.get("Authorization", "")
    if header.casefold().startswith(BEARER_PREFIX):
        return header[len(BEARER_PREFIX) :].strip() or None
    return None


def invalid_session_response() -> Response:
    response = problem_response(
        status=401,
        code="sesion_invalida",
        title="Sesión no válida",
        detail="Inicia sesión nuevamente.",
    )
    response.headers["WWW-Authenticate"] = "Bearer"
    return response
