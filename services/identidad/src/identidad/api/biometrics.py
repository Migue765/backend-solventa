from collections.abc import Callable
from functools import wraps
from typing import Any, Literal, cast
from uuid import UUID

from flask import Blueprint, Response, current_app, g, jsonify, request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge, UnsupportedMediaType

from identidad.api.schemas import tokens_to_json
from identidad.application.biometric_service import BiometricService
from identidad.config import Config
from identidad.domain.biometric import BiometricChallenge, BiometricError
from identidad.domain.errors import InvalidCredentialsError, TooManyAttemptsError
from identidad.infrastructure.biometric_crypto import b64u
from identidad.problems import problem_response

bp = Blueprint("biometrics", __name__, url_prefix="/v1/biometria")
UUID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"


class EnrollmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    contrasena: str = Field(min_length=8, max_length=128)
    clavePublica: str = Field(min_length=122, max_length=122)


class CredentialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    credencialId: str = Field(pattern=UUID_PATTERN)


class ProofRequest(CredentialRequest):
    desafioId: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    firma: str = Field(min_length=8, max_length=96)


class BiometricTokenRequest(ProofRequest):
    grant_type: Literal["biometric_signature"]
    client_id: Literal["movil"]


def service() -> BiometricService:
    return cast(BiometricService, current_app.extensions["biometric_service"])


def _bearer() -> str:
    header = request.headers.get("Authorization", "")
    if not header.lower().startswith("bearer ") or not header[7:].strip():
        raise BiometricError("sesion_invalida", 401)
    return header[7:].strip()


def _problem(code: str, status: int) -> Response:
    details = {
        "credenciales_invalidas": "Las credenciales no son validas.",
        "sesion_invalida": "Inicia sesion nuevamente.",
        "solicitud_invalida": "Revisa los datos enviados.",
        "acceso_denegado": "Esta operacion requiere una cuenta cliente.",
        "credencial_ya_registrada": "Registra una clave nueva.",
        "demasiados_intentos": "Espera antes de intentar nuevamente.",
    }
    response = problem_response(status, code, "Acceso biometrico", details[code])
    if code == "sesion_invalida":
        response.headers["WWW-Authenticate"] = "Bearer"
    return response


def guarded(operation: str) -> Callable[[Callable[..., Response]], Callable[..., Response]]:
    def decorate(function: Callable[..., Response]) -> Callable[..., Response]:
        @wraps(function)
        def run(*args: Any, **kwargs: Any) -> Response:
            request.max_content_length = 8192
            config = cast(Config, current_app.extensions["identidad_config"])
            if not config.biometric_login_enabled:
                return problem_response(
                    503,
                    "servicio_no_disponible",
                    "Servicio no disponible",
                    "El acceso biometrico no esta habilitado.",
                )
            retry: int | None = None
            try:
                body = request.get_data()
                if request.method in {"GET", "DELETE"} and body:
                    raise BiometricError("solicitud_invalida", 400)
                return function(*args, **kwargs)
            except (ValidationError, BadRequest, UnsupportedMediaType, RequestEntityTooLarge):
                code, status = "solicitud_invalida", 400
            except InvalidCredentialsError:
                code, status = "credenciales_invalidas", 401
            except BiometricError as error:
                code, status = error.code, error.status
            except TooManyAttemptsError as error:
                code, status, retry = "demasiados_intentos", 429, error.retry_after
            service().repository.audit(
                operation, code, current_app.extensions["clock"](), str(g.correlation_id)
            )
            current_app.logger.warning(
                "Biometria: %s", code, extra={"correlationId": str(g.correlation_id)}
            )
            response = _problem(code, status)
            if retry is not None:
                response.headers["Retry-After"] = str(retry)
            return response

        return run

    return decorate


def _challenge_json(challenge: BiometricChallenge) -> Response:
    service().repository.audit(
        "desafio",
        "permitido",
        current_app.extensions["clock"](),
        str(g.correlation_id),
        challenge.user_id,
        challenge.credential_id,
    )
    return jsonify(
        {
            "credencialId": str(challenge.credential_id),
            "desafioId": challenge.challenge_id,
            "proposito": challenge.purpose,
            "algoritmo": "ECDSA_P256_SHA256_DER",
            "mensaje": b64u(challenge.message),
            "expiraEn": challenge.expires_at.isoformat().replace("+00:00", "Z"),
        }
    )


@bp.post("/enrolamientos/desafio")
@guarded("alta-desafio")
def enrollment_challenge() -> Response:
    payload = EnrollmentRequest.model_validate(request.get_json())
    return _challenge_json(
        service().enrollment_challenge(_bearer(), payload.contrasena, payload.clavePublica)
    )


@bp.post("/enrolamientos")
@guarded("enrolar")
def enroll() -> Response:
    payload = ProofRequest.model_validate(request.get_json())
    service().enroll(
        _bearer(),
        UUID(payload.credencialId),
        payload.desafioId,
        payload.firma,
        str(g.correlation_id),
    )
    response = jsonify({"credencialId": payload.credencialId, "estado": "activa"})
    response.status_code = 201
    return response


@bp.post("/desafios")
@guarded("login-desafio")
def login_challenge() -> Response:
    payload = CredentialRequest.model_validate(request.get_json())
    return _challenge_json(service().login_challenge(UUID(payload.credencialId)))


@guarded("login")
def biometric_token(body: dict[str, Any]) -> Response:
    if not request.is_json or len(request.get_data()) > 8192:
        raise BiometricError("solicitud_invalida", 400)
    payload = BiometricTokenRequest.model_validate(body)
    issued = service().login(
        UUID(payload.credencialId), payload.desafioId, payload.firma, str(g.correlation_id)
    )
    return jsonify(tokens_to_json(issued))


@bp.get("/credenciales")
@guarded("listar")
def credentials() -> Response:
    rows = service().credentials(_bearer())
    service().repository.audit(
        "listar", "permitido", current_app.extensions["clock"](), str(g.correlation_id)
    )
    return jsonify(
        {
            "credenciales": [
                {
                    "credencialId": str(row.credential_id),
                    "creadaEn": row.created_at.isoformat().replace("+00:00", "Z"),
                    "ultimoUsoEn": (
                        row.last_used_at.isoformat().replace("+00:00", "Z")
                        if row.last_used_at
                        else None
                    ),
                    "estado": "revocada" if row.revoked_at else "activa",
                }
                for row in rows
            ]
        }
    )


@bp.delete("/credenciales/<uuid:credential_id>")
@guarded("revocar")
def revoke(credential_id: UUID) -> Response:
    service().revoke(_bearer(), credential_id, str(g.correlation_id))
    return Response(status=204)
