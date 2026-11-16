from typing import cast

from flask import Blueprint, Response, current_app, g, jsonify, request
from pydantic import ValidationError
from werkzeug.exceptions import RequestEntityTooLarge

from identidad.api.request_body import read_body
from identidad.api.schemas import EncryptedRegisterRequest, RegisterRequest, user_created_to_json
from identidad.application.registration_service import RegistrationService
from identidad.config import Config
from identidad.domain.errors import (
    DocumentAlreadyRegisteredError,
    EmailAlreadyRegisteredError,
    TooManyAttemptsError,
)
from identidad.infrastructure.encrypted_login import EncryptedLogin, InvalidEnvelopeError
from identidad.problems import problem_response

bp = Blueprint("users", __name__, url_prefix="/v1/usuarios")


@bp.after_request
def no_store(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.get("/desafio")
def challenge() -> Response:
    if not cast(Config, current_app.extensions["identidad_config"]).encrypted_login_enabled:
        return unavailable()
    encrypted = cast(EncryptedLogin, current_app.extensions["encrypted_login"])
    response = jsonify(encrypted.challenge("register"))
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


@bp.post("")
def register() -> Response:
    request.max_content_length = 16384
    encrypted_payload = None
    try:
        body = read_body()
        if "credencialCifrada" in body:
            if not cast(Config, current_app.extensions["identidad_config"]).encrypted_login_enabled:
                return unavailable()
            encrypted_payload = EncryptedRegisterRequest.model_validate(body)
            if encrypted_payload.fecha_nacimiento >= current_app.extensions["clock"]().date():
                raise ValueError("Fecha de nacimiento debe ser anterior a hoy.")
            encrypted = cast(EncryptedLogin, current_app.extensions["encrypted_login"])
            password = encrypted.decrypt_and_consume(
                str(encrypted_payload.correo), encrypted_payload.credencialCifrada, "register"
            )
            payload = RegisterRequest.model_validate(
                {
                    **encrypted_payload.model_dump(
                        by_alias=True, exclude={"credencialCifrada", "consentimiento"}
                    ),
                    "contrasena": password,
                }
            )
        else:
            payload = RegisterRequest.model_validate(body)
    except (ValidationError, ValueError, InvalidEnvelopeError, RequestEntityTooLarge):
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
            payload.telefono,
            payload.fecha_nacimiento,
            consent_authorized=(
                encrypted_payload.consentimiento.autorizado if encrypted_payload else None
            ),
            correlation_id=str(g.correlation_id),
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


def unavailable() -> Response:
    return problem_response(
        503, "servicio_no_disponible", "Servicio no disponible", "Registro cifrado no disponible."
    )


def too_many_attempts(error: TooManyAttemptsError) -> Response:
    response = problem_response(
        status=429,
        code="demasiados_intentos",
        title="Demasiados intentos",
        detail="Espera antes de intentar nuevamente.",
    )
    response.headers["Retry-After"] = str(error.retry_after)
    return response
