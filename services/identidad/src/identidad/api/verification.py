from collections.abc import Callable
from functools import wraps
from typing import Any, cast
from uuid import UUID

import jwt
from flask import Blueprint, Response, current_app, g, jsonify, request
from pydantic import ValidationError
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge, UnsupportedMediaType

from identidad.application.token_service import TokenService
from identidad.config import Config
from identidad.domain.verification import (
    StartVerification,
    VerificationAttempt,
    VerificationError,
)
from identidad.infrastructure.jwt_issuer import RsaJwtIssuer
from identidad.infrastructure.persistence.user_repository import SqlUserRepository
from identidad.infrastructure.persistence.verification_repository import SqlVerificationRepository
from identidad.problems import problem_response

bp = Blueprint("verification", __name__, url_prefix="/v1/verificaciones-identidad")
DETAILS = {
    "solicitud_invalida": "Usa referencias sinteticas; no envies imagenes ni datos personales.",
    "sesion_invalida": "Inicia sesion nuevamente.",
    "acceso_denegado": "Esta operacion requiere una cuenta cliente movil.",
    "verificacion_no_encontrada": "No existe la verificacion solicitada.",
    "transicion_invalida": "La verificacion es terminal; no admite mas intentos.",
    "conflicto_idempotencia": "El intentoId ya se utilizo con otras referencias.",
}


def _owner() -> UUID:
    header = request.headers.get("Authorization", "")
    config = cast(Config, current_app.extensions["identidad_config"])
    issuer = cast(RsaJwtIssuer, current_app.extensions["jwt_issuer"])
    try:
        if not header.lower().startswith("bearer "):
            raise ValueError("Bearer requerido")
        claims = jwt.decode(
            header[7:].strip(),
            jwt.PyJWK.from_dict(issuer.jwks()["keys"][0]).key,
            algorithms=["RS256"],
            issuer=config.jwt_issuer,
            audience=config.jwt_audience,
            options={"require": ["iss", "aud", "exp", "iat", "sub", "sid", "client_id"]},
        )
        owner, session_id = UUID(claims["sub"]), UUID(claims["sid"])
        if claims["client_id"] != "movil":
            raise ValueError("Canal invalido")
    except (jwt.PyJWTError, ValueError, TypeError, AttributeError) as error:
        raise VerificationError("sesion_invalida", 401) from error
    tokens = cast(TokenService, current_app.extensions["token_service"])
    users = cast(SqlUserRepository, current_app.extensions["user_repository"])
    user = users.find_by_id(owner)
    if user is None or not tokens.is_session_active(session_id):
        raise VerificationError("sesion_invalida", 401)
    if user.role != "cliente" or claims.get("scope") != "cliente":
        raise VerificationError("acceso_denegado", 403)
    return owner


def _repository() -> SqlVerificationRepository:
    return cast(SqlVerificationRepository, current_app.extensions["verification_repository"])


def guarded(function: Callable[..., Response]) -> Callable[..., Response]:
    @wraps(function)
    def run(*args: Any, **kwargs: Any) -> Response:
        request.max_content_length = 2048
        config = cast(Config, current_app.extensions["identidad_config"])
        try:
            owner = _owner()
            if not config.simulated_identity_enabled:
                return problem_response(
                    503,
                    "servicio_no_disponible",
                    "Servicio no disponible",
                    "La demostracion academica de identidad no esta habilitada.",
                )
            if request.args or (request.method == "GET" and request.get_data()):
                raise VerificationError("solicitud_invalida", 400)
            return function(owner, *args, **kwargs)
        except (ValidationError, BadRequest, UnsupportedMediaType, RequestEntityTooLarge):
            code, status = "solicitud_invalida", 400
        except VerificationError as error:
            code, status = error.code, error.status
        current_app.logger.warning(
            "Identidad simulada: %s", code, extra={"correlationId": str(g.correlation_id)}
        )
        response = problem_response(status, code, "Verificacion simulada", DETAILS[code])
        if status == 401:
            response.headers["WWW-Authenticate"] = "Bearer"
        return response

    return run


@bp.post("")
@guarded
def start(owner: UUID) -> Response:
    StartVerification.model_validate(request.get_json())
    return jsonify(_repository().start(owner))


@bp.get("/<uuid:verification_id>")
@guarded
def get(owner: UUID, verification_id: UUID) -> Response:
    return jsonify(_repository().get(owner, verification_id))


@bp.post("/<uuid:verification_id>/intentos")
@guarded
def submit(owner: UUID, verification_id: UUID) -> Response:
    payload = VerificationAttempt.model_validate(request.get_json())
    return jsonify(_repository().submit(owner, verification_id, payload))
