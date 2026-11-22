import hashlib
from ipaddress import ip_address
from typing import cast

from flask import Blueprint, Response, current_app, jsonify, request
from pydantic import BaseModel, ConfigDict, EmailStr, Field, ValidationError
from werkzeug.exceptions import RequestEntityTooLarge

from identidad.api.advisors import _authorized
from identidad.api.oauth import _invalid_request, _unavailable
from identidad.api.request_body import read_body
from identidad.api.users import too_many_attempts
from identidad.application.password_recovery_service import (
    InvalidRecoveryError,
    PasswordRecoveryService,
    RecoveryUnavailableError,
)
from identidad.application.ports import Clock, LoginAttemptLimiter
from identidad.config import Config
from identidad.domain.errors import TooManyAttemptsError
from identidad.infrastructure.encrypted_login import EncryptedLogin, InvalidEnvelopeError
from identidad.problems import problem_response

bp = Blueprint("password_recovery", __name__, url_prefix="/v1/recuperacion-contrasena")
GENERIC_MESSAGE = "Si existe una cuenta con ese correo, recibiras un enlace de recuperacion."


class RecoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    correo: EmailStr = Field(max_length=254)


class RecoveryConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(pattern=r"^[A-Za-z0-9_-]{64}$", strict=True)
    credencialCifrada: str = Field(min_length=1, max_length=8192, strict=True)


@bp.post("")
def request_recovery() -> Response:
    request.max_content_length = 2048
    try:
        _limit("request")
        payload = RecoveryRequest.model_validate(read_body())
        _service().request(str(payload.correo))
    except (ValidationError, RequestEntityTooLarge):
        return _invalid_request()
    except TooManyAttemptsError as error:
        return too_many_attempts(error)
    response = jsonify({"mensaje": GENERIC_MESSAGE})
    response.status_code = 202
    return response


@bp.get("/desafio")
def challenge() -> Response:
    try:
        _limit("challenge")
    except TooManyAttemptsError as error:
        return too_many_attempts(error)
    if not _config().encrypted_login_enabled:
        return _unavailable()
    return jsonify(_encryption().challenge("reset"))


@bp.post("/confirmar")
def confirm() -> Response:
    request.max_content_length = 16384
    try:
        _limit("confirm")
        payload = RecoveryConfirmation.model_validate(read_body())
        if not _config().encrypted_login_enabled:
            return _unavailable()
        password = _encryption().decrypt_reset_and_consume(payload.token, payload.credencialCifrada)
        _service().confirm(payload.token, password)
    except (ValidationError, RequestEntityTooLarge, InvalidEnvelopeError, ValueError):
        return _invalid_request()
    except TooManyAttemptsError as error:
        return too_many_attempts(error)
    except InvalidRecoveryError:
        return problem_response(
            status=400,
            code="recuperacion_invalida",
            title="Enlace no valido",
            detail="Solicita un nuevo enlace de recuperacion.",
        )
    return Response(status=204)


@bp.app_errorhandler(RecoveryUnavailableError)
def unavailable(_: RecoveryUnavailableError) -> Response:
    current_app.logger.error("Identidad: transporte de recuperacion no disponible")
    return problem_response(
        status=503,
        code="servicio_no_disponible",
        title="Servicio no disponible",
        detail="La recuperacion de contrasena no esta disponible.",
    )


def _limit(operation: str) -> None:
    limiter = cast(LoginAttemptLimiter, current_app.extensions["recovery_limiter"])
    now = cast(Clock, current_app.extensions["clock"])()
    peer = request.remote_addr or "unknown"
    forwarded = request.headers.get("X-Recovery-Client-IP")
    if forwarded and _authorized():
        try:
            peer = str(ip_address(forwarded))
        except ValueError:
            raise RecoveryUnavailableError() from None
    address = hashlib.sha256(peer.encode()).hexdigest()
    key = f"recovery:ip:{operation}:{address}"
    limiter.ensure_allowed(key, now)
    limiter.register_failure(key, now)


def _service() -> PasswordRecoveryService:
    return cast(PasswordRecoveryService, current_app.extensions["password_recovery_service"])


def _config() -> Config:
    return cast(Config, current_app.extensions["identidad_config"])


def _encryption() -> EncryptedLogin:
    return cast(EncryptedLogin, current_app.extensions["encrypted_login"])
