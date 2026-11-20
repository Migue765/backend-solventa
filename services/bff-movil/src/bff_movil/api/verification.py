from collections.abc import Callable
from functools import wraps
from typing import Any, Literal, cast
from uuid import UUID

from flask import Blueprint, Response, current_app, jsonify, request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge, UnsupportedMediaType

from bff_movil.api.identity import correlation_id, invalid_request_response, rejection_response
from bff_movil.api.security import authenticate, bearer_token, invalid_session_response
from bff_movil.application.verification_ports import VerificationGateway
from bff_movil.config import Config
from bff_movil.domain.errors import (
    IdentityRejectedError,
    IdentityUnavailableError,
    InvalidAccessTokenError,
)
from bff_movil.problems import problem_response

bp = Blueprint("verification", __name__, url_prefix="/movil/v1/verificaciones-identidad")
UUID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"


class StartVerification(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class VerificationAttempt(StartVerification):
    intentoId: str = Field(pattern=UUID_PATTERN)
    documentoRef: Literal["fixture:documento-v1", "fixture:documento-v2"]
    selfieRef: Literal["fixture:selfie-v1", "fixture:selfie-v2"]


class VerificationResponse(StartVerification):
    verificacionId: str = Field(pattern=UUID_PATTERN)
    estado: Literal["pendiente", "aprobado", "rechazado"]
    intentos: int = Field(ge=0, le=3)
    maxIntentos: Literal[3]
    reintentoPermitido: bool
    simulada: Literal[True]
    motivo: Literal["documento_no_valido", "prueba_vida_no_valida"] | None

    @model_validator(mode="after")
    def coherent_state(self) -> "VerificationResponse":
        if self.reintentoPermitido != (self.estado == "rechazado" and self.intentos < 3):
            raise ValueError("Reintento incoherente")
        if (self.estado == "pendiente") != (self.intentos == 0):
            raise ValueError("Contador incoherente")
        if (self.estado == "rechazado") != (self.motivo is not None):
            raise ValueError("Motivo incoherente")
        return self


def guarded(function: Callable[..., Response]) -> Callable[..., Response]:
    @wraps(function)
    def run(*args: Any, **kwargs: Any) -> Response:
        request.max_content_length = 2048
        try:
            claims = authenticate()
            if claims.user.role != "cliente" or claims.scope != "cliente":
                raise IdentityRejectedError(403, "acceso_denegado")
            config = cast(Config, current_app.extensions["bff_config"])
            if not config.simulated_identity_enabled:
                return problem_response(
                    503,
                    "servicio_no_disponible",
                    "Servicio no disponible",
                    "La demostracion academica de identidad no esta habilitada.",
                )
            if request.args or (request.method == "GET" and request.get_data()):
                return invalid_request_response()
            return function(*args, **kwargs)
        except InvalidAccessTokenError:
            return invalid_session_response()
        except IdentityRejectedError as error:
            return rejection_response(error)
        except (ValidationError, BadRequest, UnsupportedMediaType, RequestEntityTooLarge):
            return invalid_request_response()

    return run


def _forward(
    method: str,
    suffix: str = "",
    body: dict[str, object] | None = None,
    verification_id: UUID | None = None,
) -> Response:
    gateway = cast(VerificationGateway, current_app.extensions["verification_gateway"])
    bearer = bearer_token()
    if bearer is None:
        return invalid_session_response()
    data = gateway.verification_request(
        method, "/v1/verificaciones-identidad" + suffix, body, bearer, correlation_id()
    )
    try:
        result = VerificationResponse.model_validate(data)
    except ValidationError as error:
        raise IdentityUnavailableError() from error
    if verification_id is not None and result.verificacionId != str(verification_id):
        raise IdentityUnavailableError()
    return jsonify(result.model_dump())


@bp.post("")
@guarded
def start() -> Response:
    payload = StartVerification.model_validate(request.get_json())
    return _forward("POST", body=payload.model_dump())


@bp.get("/<uuid:verification_id>")
@guarded
def get(verification_id: UUID) -> Response:
    return _forward("GET", f"/{verification_id}", verification_id=verification_id)


@bp.post("/<uuid:verification_id>/intentos")
@guarded
def submit(verification_id: UUID) -> Response:
    payload = VerificationAttempt.model_validate(request.get_json())
    return _forward("POST", f"/{verification_id}/intentos", payload.model_dump(), verification_id)
