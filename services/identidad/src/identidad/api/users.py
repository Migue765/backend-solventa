from typing import cast

from flask import Blueprint, Response, current_app, jsonify
from pydantic import ValidationError

from identidad.api.request_body import read_body
from identidad.api.schemas import RegisterRequest, user_created_to_json
from identidad.application.registration_service import RegistrationService
from identidad.domain.errors import (
    DocumentAlreadyRegisteredError,
    EmailAlreadyRegisteredError,
    TooManyAttemptsError,
)
from identidad.problems import problem_response

bp = Blueprint("users", __name__, url_prefix="/v1/usuarios")


@bp.post("")
def register() -> Response:
    try:
        payload = RegisterRequest.model_validate(read_body())
    except ValidationError:
        return problem_response(
            status=400,
            code="solicitud_invalida",
            title="Solicitud inválida",
            detail="Revisa los datos enviados.",
        )

    service = cast(RegistrationService, current_app.extensions["registration_service"])
    try:
        user = service.register(
            payload.nombre,
            str(payload.correo),
            payload.contrasena,
            payload.tipo_documento,
            payload.numero_documento,
        )
    except EmailAlreadyRegisteredError:
        return problem_response(
            status=409,
            code="correo_ya_registrado",
            title="Correo ya registrado",
            detail="Ya existe una cuenta con ese correo.",
        )
    except DocumentAlreadyRegisteredError:
        return problem_response(
            status=409,
            code="documento_ya_registrado",
            title="Documento ya registrado",
            detail="Ya existe una cuenta con ese documento.",
        )
    except TooManyAttemptsError as error:
        return too_many_attempts(error)

    response = jsonify(user_created_to_json(user))
    response.status_code = 201
    response.headers["Cache-Control"] = "no-store"
    return response


def too_many_attempts(error: TooManyAttemptsError) -> Response:
    response = problem_response(
        status=429,
        code="demasiados_intentos",
        title="Demasiados intentos",
        detail="Espera antes de intentar nuevamente.",
    )
    response.headers["Retry-After"] = str(error.retry_after)
    return response
