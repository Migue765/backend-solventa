from typing import cast

from flask import Response, current_app, g

from bff_web.application.ports import Clock, IdentityGateway
from bff_web.domain.errors import IdentityRejectedError
from bff_web.problems import problem_response

REJECTIONS = {
    "solicitud_invalida": ("Solicitud inválida", "Revisa los datos enviados."),
    "credenciales_invalidas": (
        "No fue posible iniciar sesión",
        "Las credenciales no son válidas.",
    ),
    "refresh_invalido": ("Sesión no válida", "Inicia sesión nuevamente."),
    "correo_ya_registrado": ("Correo ya registrado", "Ya existe una cuenta con ese correo."),
    "demasiados_intentos": ("Demasiados intentos", "Espera antes de intentar nuevamente."),
}


def identity_gateway() -> IdentityGateway:
    return cast(IdentityGateway, current_app.extensions["identity_gateway"])


def clock() -> Clock:
    return cast(Clock, current_app.extensions["clock"])


def correlation_id() -> str:
    return str(g.correlation_id)


def rejection_response(error: IdentityRejectedError) -> Response:
    title, detail = REJECTIONS[error.code]
    response = problem_response(status=error.status, code=error.code, title=title, detail=detail)
    if error.retry_after is not None:
        response.headers["Retry-After"] = str(error.retry_after)
    return response


def invalid_request_response() -> Response:
    title, detail = REJECTIONS["solicitud_invalida"]
    return problem_response(status=400, code="solicitud_invalida", title=title, detail=detail)
