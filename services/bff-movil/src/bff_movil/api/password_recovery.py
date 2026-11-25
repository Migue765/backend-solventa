from typing import Literal, cast

from flask import Blueprint, Response, current_app, jsonify, request
from pydantic import BaseModel, ConfigDict, EmailStr, Field, ValidationError
from werkzeug.exceptions import RequestEntityTooLarge

from bff_movil.api.identity import correlation_id, invalid_request_response, rejection_response
from bff_movil.config import Config
from bff_movil.domain.errors import IdentityRejectedError, IdentityUnavailableError
from bff_movil.infrastructure.http_identity_gateway import HttpIdentityGateway

BASE = "/v1/recuperacion-contrasena"
bp = Blueprint("password_recovery", __name__, url_prefix="/movil" + BASE)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class RecoveryRequest(StrictModel):
    correo: EmailStr = Field(max_length=254)


class Confirmation(StrictModel):
    token: str = Field(pattern=r"^[A-Za-z0-9_-]{64}$")
    credencialCifrada: str = Field(min_length=1, max_length=8192)


class Accepted(StrictModel):
    mensaje: Literal[
        "Si existe una cuenta con ese correo, recibiras un enlace de recuperacion.",
        "Si existe una cuenta con ese correo, el administrador recibira un enlace de recuperacion.",
    ]


class PublicKey(StrictModel):
    kty: Literal["RSA"]
    alg: Literal["RSA-OAEP-256"]
    use: Literal["enc"]
    kid: str
    n: str
    e: str


class Challenge(StrictModel):
    desafioId: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    expiraEn: str
    clavePublica: PublicKey


def _forward(
    suffix: str, status: int, schema: type[StrictModel] | None, body: StrictModel | None = None
) -> Response:
    gateway = cast(HttpIdentityGateway, current_app.extensions["recovery_gateway"])
    config = cast(Config, current_app.extensions["bff_config"])
    if not request.remote_addr or not config.internal_api_token:
        raise IdentityUnavailableError()
    try:
        result = gateway.recovery_request(
            BASE + suffix,
            body.model_dump() if body else None,
            config.internal_api_token,
            correlation_id(),
            request.remote_addr,
            status,
        )
        if schema is None:
            return Response(status=204)
        response = jsonify(schema.model_validate(result).model_dump())
        response.status_code = status
        return response
    except IdentityRejectedError as error:
        return rejection_response(error)
    except ValidationError:
        raise IdentityUnavailableError() from None


@bp.post("")
def request_recovery() -> Response:
    request.max_content_length = 2048
    try:
        payload = RecoveryRequest.model_validate(request.get_json())
    except (ValidationError, RequestEntityTooLarge):
        return invalid_request_response()
    return _forward("", 202, Accepted, payload)


@bp.get("/desafio")
def challenge() -> Response:
    return _forward("/desafio", 200, Challenge)


@bp.post("/confirmar")
def confirm() -> Response:
    request.max_content_length = 16384
    try:
        payload = Confirmation.model_validate(request.get_json())
    except (ValidationError, RequestEntityTooLarge):
        return invalid_request_response()
    return _forward("/confirmar", 204, None, payload)
