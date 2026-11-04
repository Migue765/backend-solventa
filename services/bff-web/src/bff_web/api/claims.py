from typing import cast
from uuid import UUID

from flask import Blueprint, Response, current_app, g, jsonify, request
from pydantic import ValidationError

from bff_web.api.security import authenticate, invalid_session_response
from bff_web.application.claim_ports import ClaimGateway
from bff_web.domain.claims import ClaimDraft, ClaimError
from bff_web.domain.errors import InvalidAccessTokenError
from bff_web.problems import problem_response

bp = Blueprint("claims", __name__, url_prefix="/v1/siniestros")


@bp.before_request
def authorize() -> Response | None:
    try:
        claims = authenticate()
    except InvalidAccessTokenError:
        return invalid_session_response()
    if claims.user.role != "cliente":
        return problem_response(
            403,
            "alcance_insuficiente",
            "Acceso denegado",
            "Se requiere una cuenta de cliente.",
        )
    g.claim_user_id = claims.user.user_id
    if request.args or (request.method == "GET" and request.get_data()):
        return problem_response(
            400,
            "solicitud_invalida",
            "Solicitud invalida",
            "El cliente se obtiene exclusivamente de la sesion autenticada.",
        )
    return None


@bp.after_request
def no_store(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.post("")
def create() -> tuple[Response, int] | Response:
    try:
        draft = ClaimDraft.model_validate(request.get_json())
        key = UUID(request.headers.get("Idempotency-Key", ""))
    except (ValueError, ValidationError):
        return problem_response(
            400,
            "solicitud_invalida",
            "Solicitud invalida",
            "Revisa la poliza, fecha del evento, descripcion y clave de idempotencia.",
        )
    result, created = _gateway().create(g.claim_user_id, draft, key, str(g.correlation_id))
    response = jsonify(result.model_dump(mode="json", by_alias=True))
    response.headers["Location"] = f"/v1/siniestros/{result.id}"
    return response, 201 if created else 200


@bp.get("/polizas")
def policies() -> Response:
    result = _gateway().policies(g.claim_user_id, str(g.correlation_id))
    return jsonify(result.model_dump(mode="json", by_alias=True))


@bp.get("")
def list_claims() -> Response:
    result = _gateway().list(g.claim_user_id, str(g.correlation_id))
    return jsonify(result.model_dump(mode="json", by_alias=True))


@bp.get("/<claim_id>")
def get(claim_id: str) -> Response:
    try:
        identifier = UUID(claim_id)
    except ValueError:
        return problem_response(
            400, "solicitud_invalida", "Solicitud invalida", "El identificador debe ser UUID."
        )
    result = _gateway().get(g.claim_user_id, identifier, str(g.correlation_id))
    return jsonify(result.model_dump(mode="json", by_alias=True))


@bp.errorhandler(ClaimError)
def error_response(error: ClaimError) -> Response:
    if error.status == 503:
        current_app.logger.exception(
            "Fallo del servicio de siniestros", extra={"correlationId": str(g.correlation_id)}
        )
    response = problem_response(
        error.status,
        error.code,
        "No se pudo completar la operacion",
        "Verifica los datos o reintenta conservando la misma clave de idempotencia.",
    )
    if error.status == 503:
        response.headers["Retry-After"] = "5"
    return response


def _gateway() -> ClaimGateway:
    return cast(ClaimGateway, current_app.extensions["claim_gateway"])
