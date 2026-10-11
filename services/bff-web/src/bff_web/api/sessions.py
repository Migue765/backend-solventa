from typing import cast

from flask import Blueprint, Response, current_app, jsonify, request
from pydantic import ValidationError

from bff_web.api.schemas import LoginRequest, session_to_json
from bff_web.application.auth_service import AuthService
from bff_web.config import Config
from bff_web.domain.errors import (
    InvalidCredentialsError,
    TooManyAttemptsError,
    UnauthorizedError,
)
from bff_web.problems import problem_response

bp = Blueprint("sessions", __name__, url_prefix="/v1/sesiones")


@bp.post("")
def login() -> Response:
    try:
        payload = LoginRequest.model_validate(request.get_json(silent=False))
    except (ValidationError, TypeError):
        return problem_response(
            status=400,
            code="solicitud_invalida",
            title="Solicitud inválida",
            detail="Revisa los datos enviados.",
        )

    try:
        started = _auth_service().login(
            str(payload.correo),
            payload.contrasena,
            _attempt_key(str(payload.correo)),
        )
    except InvalidCredentialsError:
        return problem_response(
            status=401,
            code="credenciales_invalidas",
            title="No fue posible iniciar sesión",
            detail="Las credenciales no son válidas.",
        )
    except TooManyAttemptsError as error:
        response = problem_response(
            status=429,
            code="demasiados_intentos",
            title="Demasiados intentos",
            detail="Espera antes de intentar nuevamente.",
        )
        response.headers["Retry-After"] = str(error.retry_after)
        return response

    response = jsonify(session_to_json(started.session))
    response.set_cookie(
        _config().session_cookie_name,
        started.token,
        max_age=int(_config().session_duration.total_seconds()),
        secure=_config().session_cookie_secure,
        httponly=True,
        samesite=_config().session_cookie_samesite,
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.get("/actual")
def current_session() -> Response:
    try:
        session = _auth_service().current_session(_session_token())
    except UnauthorizedError:
        return problem_response(
            status=401,
            code="sesion_invalida",
            title="Sesión no válida",
            detail="Inicia sesión nuevamente.",
        )

    response = jsonify(session_to_json(session))
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.delete("/actual")
def logout() -> Response:
    token = _session_token()
    try:
        _auth_service().current_session(token)
    except UnauthorizedError:
        return problem_response(
            status=401,
            code="sesion_invalida",
            title="Sesión no válida",
            detail="Inicia sesión nuevamente.",
        )

    _auth_service().logout(token)
    response = Response(status=204)
    response.delete_cookie(
        _config().session_cookie_name,
        secure=_config().session_cookie_secure,
        httponly=True,
        samesite=_config().session_cookie_samesite,
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return response


def _auth_service() -> AuthService:
    return cast(AuthService, current_app.extensions["auth_service"])


def _config() -> Config:
    return cast(Config, current_app.extensions["bff_config"])


def _session_token() -> str | None:
    return request.cookies.get(_config().session_cookie_name)


def _attempt_key(email: str) -> str:
    remote_address = request.remote_addr or "unknown"
    return f"{remote_address}:{email.casefold()}"
