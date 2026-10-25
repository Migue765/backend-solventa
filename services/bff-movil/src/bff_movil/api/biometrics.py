from collections.abc import Callable
from functools import wraps
from typing import Any, Literal, cast
from uuid import UUID

from flask import Blueprint, Response, current_app, jsonify, request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge, UnsupportedMediaType

from bff_movil.api.identity import correlation_id, invalid_request_response, rejection_response
from bff_movil.api.security import authenticate, bearer_token, invalid_session_response
from bff_movil.api.sessions import _tokens_response
from bff_movil.application.biometric_ports import BiometricGateway
from bff_movil.config import Config
from bff_movil.domain.errors import (
    IdentityRejectedError,
    IdentityUnavailableError,
    InvalidAccessTokenError,
)
from bff_movil.problems import problem_response

bp = Blueprint("biometrics", __name__)
UUID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"


class EnrollmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    contrasena: str = Field(min_length=8, max_length=128)
    clavePublica: str = Field(pattern=r"^[A-Za-z0-9_-]{122}$")


class CredentialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    credencialId: str = Field(pattern=UUID_PATTERN)


class ProofRequest(CredentialRequest):
    desafioId: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    firma: str = Field(pattern=r"^[A-Za-z0-9_-]{8,96}$")


class ChallengeResponse(CredentialRequest):
    desafioId: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    proposito: Literal["enrolar", "login"]
    algoritmo: Literal["ECDSA_P256_SHA256_DER"]
    mensaje: str = Field(pattern=r"^[A-Za-z0-9_-]{1,683}$")
    expiraEn: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T.*Z$")


class EnrollmentResponse(CredentialRequest):
    estado: Literal["activa"]


class ListedCredential(CredentialRequest):
    creadaEn: str
    ultimoUsoEn: str | None
    estado: Literal["activa", "revocada"]


class ListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    credenciales: list[ListedCredential]


def _gateway() -> BiometricGateway:
    return cast(BiometricGateway, current_app.extensions["biometric_gateway"])


def guarded(
    protected: bool = False,
) -> Callable[[Callable[..., Response]], Callable[..., Response]]:
    def decorate(function: Callable[..., Response]) -> Callable[..., Response]:
        @wraps(function)
        def run(*args: Any, **kwargs: Any) -> Response:
            request.max_content_length = 8192
            config = cast(Config, current_app.extensions["bff_config"])
            if not config.biometric_login_enabled:
                return problem_response(
                    503,
                    "servicio_no_disponible",
                    "Servicio no disponible",
                    "El acceso biometrico no esta habilitado.",
                )
            try:
                body = request.get_data()
                if request.method in {"GET", "DELETE"} and body:
                    return invalid_request_response()
                if protected:
                    claims = authenticate()
                    if claims.user.role != "cliente":
                        return rejection_response(IdentityRejectedError(403, "acceso_denegado"))
                return function(*args, **kwargs)
            except InvalidAccessTokenError:
                return invalid_session_response()
            except IdentityRejectedError as error:
                return rejection_response(error)
            except (ValidationError, BadRequest, UnsupportedMediaType, RequestEntityTooLarge):
                return invalid_request_response()

        return run

    return decorate


def _validated(data: dict[str, Any], schema: type[BaseModel]) -> dict[str, Any]:
    try:
        return schema.model_validate(data).model_dump(mode="json")
    except ValidationError as error:
        raise IdentityUnavailableError() from error


@bp.post("/movil/v1/biometria/enrolamientos/desafio")
@guarded(protected=True)
def enrollment_challenge() -> Response:
    payload = EnrollmentRequest.model_validate(request.get_json())
    data = _gateway().biometric_request(
        "POST",
        "/v1/biometria/enrolamientos/desafio",
        payload.model_dump(),
        bearer_token(),
        correlation_id(),
    )
    result = _validated(data, ChallengeResponse)
    if result["proposito"] != "enrolar":
        raise IdentityUnavailableError()
    return jsonify(result)


@bp.post("/movil/v1/biometria/enrolamientos")
@guarded(protected=True)
def enroll() -> Response:
    payload = ProofRequest.model_validate(request.get_json())
    data = _gateway().biometric_request(
        "POST",
        "/v1/biometria/enrolamientos",
        payload.model_dump(),
        bearer_token(),
        correlation_id(),
    )
    result = _validated(data, EnrollmentResponse)
    if result["credencialId"] != payload.credencialId:
        raise IdentityUnavailableError()
    response = jsonify(result)
    response.status_code = 201
    return response


@bp.post("/movil/v1/biometria/desafios")
@guarded()
def login_challenge() -> Response:
    payload = CredentialRequest.model_validate(request.get_json())
    data = _gateway().biometric_request(
        "POST", "/v1/biometria/desafios", payload.model_dump(), None, correlation_id()
    )
    result = _validated(data, ChallengeResponse)
    if result["proposito"] != "login" or result["credencialId"] != payload.credencialId:
        raise IdentityUnavailableError()
    return jsonify(result)


@bp.post("/movil/v1/sesiones/biometria")
@guarded()
def login() -> Response:
    payload = ProofRequest.model_validate(request.get_json())
    return _tokens_response(_gateway().biometric_login(payload.model_dump(), correlation_id()))


@bp.get("/movil/v1/biometria/credenciales")
@guarded(protected=True)
def credentials() -> Response:
    data = _gateway().biometric_request(
        "GET", "/v1/biometria/credenciales", None, bearer_token(), correlation_id()
    )
    return jsonify(_validated(data, ListResponse))


@bp.delete("/movil/v1/biometria/credenciales/<uuid:credential_id>")
@guarded(protected=True)
def revoke(credential_id: UUID) -> Response:
    data = _gateway().biometric_request(
        "DELETE",
        f"/v1/biometria/credenciales/{credential_id}",
        None,
        bearer_token(),
        correlation_id(),
    )
    if data:
        raise IdentityUnavailableError()
    return Response(status=204)
