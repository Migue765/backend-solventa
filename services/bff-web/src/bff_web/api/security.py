from typing import cast

from flask import Response, current_app, request

from bff_web.application.ports import TokenVerifier
from bff_web.domain.auth import AccessClaims
from bff_web.domain.errors import InvalidAccessTokenError
from bff_web.problems import problem_response

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


def partner_claims() -> AccessClaims | Response:
    try:
        claims = authenticate()
    except InvalidAccessTokenError:
        return invalid_session_response()
    if claims.user.role != "socio" or claims.scope != "socio" or not claims.user.partner_ids:
        return problem_response(
            403, "alcance_insuficiente", "Acceso denegado", "Se requiere una cuenta de socio."
        )
    return claims
