from datetime import timedelta
from typing import cast

from flask import Blueprint, Response, current_app, jsonify, request
from pydantic import ValidationError
from werkzeug.exceptions import RequestEntityTooLarge

from bff_web.api.identity import (
    clock,
    correlation_id,
    identity_gateway,
    invalid_request_response,
    rejection_response,
)
from bff_web.api.schemas import (
    EncryptedRegisterRequest,
    RegisterRequest,
    RegistrationChallenge,
    created_user_to_json,
    user_to_json,
)
from bff_web.application.ports import IdentityGateway
from bff_web.config import Config
from bff_web.domain.errors import IdentityRejectedError, IdentityUnavailableError

bp = Blueprint("users", __name__, url_prefix="/v1/usuarios")


@bp.after_request
def no_store(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.get("/desafio")
def challenge() -> Response:
    if not _encrypted_enabled():
        raise IdentityUnavailableError()
    return registration_challenge(identity_gateway())


def registration_challenge(gateway: IdentityGateway) -> Response:
    try:
        payload = gateway.registration_challenge(correlation_id())
        validated = RegistrationChallenge.model_validate(payload)
        now = clock()()
        if not now < validated.expiraEn <= now + timedelta(seconds=60):
            raise IdentityUnavailableError()
    except IdentityRejectedError as error:
        return rejection_response(error)
    except ValidationError as error:
        raise IdentityUnavailableError() from error
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


def _encrypted_enabled() -> bool:
    return cast(Config, current_app.extensions["bff_config"]).encrypted_login_enabled


@bp.post("")
def register() -> Response:
    request.max_content_length = 16384
    try:
        if _encrypted_enabled():
            encrypted = EncryptedRegisterRequest.model_validate(request.get_json(silent=False))
            user = identity_gateway().encrypted_register(
                encrypted.model_dump(mode="json"), correlation_id()
            )
        else:
            payload = RegisterRequest.model_validate(request.get_json(silent=False))
            user = identity_gateway().register(
                payload.nombre, str(payload.correo), payload.contrasena, correlation_id()
            )
    except (ValidationError, TypeError, RequestEntityTooLarge):
        return invalid_request_response()
    except IdentityRejectedError as error:
        return rejection_response(error)

    body = created_user_to_json(user)
    if _encrypted_enabled():
        user_json = user_to_json(user)
        user_json.update(
            tipoDocumento=user.document_type,
            identificacion=user.identification,
            telefono=user.phone,
        )
        body = {"usuario": user_json}
    response = jsonify(body)
    response.status_code = 201
    response.headers["Cache-Control"] = "no-store"
    return response
