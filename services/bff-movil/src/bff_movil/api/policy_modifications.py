from typing import cast
from uuid import UUID

from flask import Blueprint, Response, current_app, g, jsonify, request
from pydantic import ValidationError

from bff_movil.api.security import authenticate, invalid_session_response
from bff_movil.application.policy_modification_ports import PolicyModificationGateway
from bff_movil.domain.errors import InvalidAccessTokenError
from bff_movil.domain.policy_modification import (
    ModificationConfirmation,
    ModificationConflictError,
    ModificationRejectedError,
    ModificationRequest,
    PolicyNotFoundError,
    PolicyNotModifiableError,
    PolicyServiceUnavailableError,
)
from bff_movil.problems import problem_response

bp = Blueprint("policy_modifications", __name__, url_prefix="/movil/v1/polizas")

MAX_BODY_BYTES = 16 * 1024
NOT_MODIFIABLE_DETAILS = {
    "producto_no_modificable": "Este producto se contrata por fechas y no admite modificaciones.",
    "poliza_programada": "La poliza aun no inicia su vigencia.",
    "poliza_vencida": "La poliza ya vencio y no admite modificaciones.",
    "fin_de_vigencia": "La poliza termina hoy; los cambios aplicarian fuera de la vigencia.",
}
REJECTION_DETAILS = {
    "cambio_no_permitido": "El cambio solicitado no esta autorizado para este producto.",
    "sin_cambios": "Las condiciones propuestas son iguales a las vigentes.",
}
CONFLICT_DETAILS = {
    "version_desactualizada": "La poliza cambio; consulta la version vigente y vuelve a simular.",
    "impacto_cambio": "El impacto cambio desde la simulacion; vuelve a revisarlo.",
    "idempotencia_conflictiva": "La clave de la operacion ya se uso con otra solicitud.",
}
CONFLICT_ACTIONS = {
    "version_desactualizada": "consultar_poliza",
    "impacto_cambio": "simular_de_nuevo",
    "idempotencia_conflictiva": "nueva_operacion",
}


@bp.after_request
def private(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    if response.status_code >= 400:
        current_app.logger.warning(
            "modificaciones.rechazo canal=movil status=%s correlationId=%s",
            response.status_code,
            g.correlation_id,
        )
    return response


@bp.get("/<policy_id>/modificacion")
def get_state(policy_id: str) -> Response:
    client_id = _client()
    if isinstance(client_id, Response):
        return client_id
    policy = _uuid(policy_id)
    if policy is None or request.args or request.get_data():
        return _invalid("La poliza se identifica con un UUID y no admite parametros.")
    try:
        state = _gateway().get_state(policy, client_id, str(g.correlation_id))
    except PolicyNotFoundError:
        return _not_found()
    except PolicyServiceUnavailableError:
        return _unavailable("No fue posible consultar las condiciones de la poliza.", "reintentar")
    return jsonify(state.model_dump(mode="json", by_alias=True))


@bp.post("/<policy_id>/modificaciones/simulacion")
def simulate(policy_id: str) -> Response:
    client_id = _client()
    if isinstance(client_id, Response):
        return client_id
    policy = _uuid(policy_id)
    if policy is None or request.args:
        return _invalid("La poliza se identifica con un UUID y no admite parametros.")
    if (too_large := _too_large()) is not None:
        return too_large
    try:
        modification = ModificationRequest.model_validate(request.get_json(silent=True))
    except ValidationError:
        return _invalid("Indica la version base, el plan y las coberturas opcionales.")
    try:
        impact = _gateway().simulate(policy, client_id, modification, str(g.correlation_id))
    except (
        PolicyNotFoundError,
        PolicyNotModifiableError,
        ModificationRejectedError,
        ModificationConflictError,
    ) as error:
        return _business_problem(error)
    except PolicyServiceUnavailableError:
        return _unavailable("No fue posible calcular el impacto de la modificacion.", "reintentar")
    return jsonify(impact.model_dump(mode="json", by_alias=True))


@bp.post("/<policy_id>/modificaciones")
def confirm(policy_id: str) -> Response:
    client_id = _client()
    if isinstance(client_id, Response):
        return client_id
    policy = _uuid(policy_id)
    key = _uuid(request.headers.get("Idempotency-Key", ""))
    if policy is None or key is None or request.args:
        return _invalid("Indica la poliza y una Idempotency-Key UUID.")
    if (too_large := _too_large()) is not None:
        return too_large
    try:
        confirmation = ModificationConfirmation.model_validate(request.get_json(silent=True))
    except ValidationError:
        return _invalid("Debes aceptar explicitamente el impacto simulado.")
    try:
        result, status = _gateway().confirm(
            policy, client_id, key, confirmation, str(g.correlation_id)
        )
    except (
        PolicyNotFoundError,
        PolicyNotModifiableError,
        ModificationRejectedError,
        ModificationConflictError,
    ) as error:
        return _business_problem(error)
    except PolicyServiceUnavailableError:
        return _unavailable(
            "No se confirmo la modificacion. Reintenta con la misma clave para conocer "
            "el resultado.",
            "reintentar_misma_clave",
        )
    response = jsonify(result.model_dump(mode="json", by_alias=True))
    response.status_code = status
    return response


def _business_problem(error: Exception) -> Response:
    if isinstance(error, PolicyNotModifiableError):
        return problem_response(
            422,
            "poliza_no_modificable",
            "Poliza no modificable",
            NOT_MODIFIABLE_DETAILS[error.reason],
            extensions={"motivo": error.reason},
        )
    if isinstance(error, ModificationRejectedError):
        extensions: dict[str, object] = {"accion": "ajustar_cambios"}
        if error.field:
            extensions["campo"] = error.field
        return problem_response(
            422,
            error.code,
            "Modificacion rechazada",
            REJECTION_DETAILS[error.code],
            extensions=extensions,
        )
    if isinstance(error, ModificationConflictError):
        extensions = {"accion": CONFLICT_ACTIONS[error.code]}
        if error.current_version is not None:
            extensions["versionActual"] = error.current_version
        return problem_response(
            409,
            error.code,
            "Conflicto de modificacion",
            CONFLICT_DETAILS[error.code],
            extensions=extensions,
        )
    return _not_found()


def _client() -> UUID | Response:
    try:
        claims = authenticate()
    except InvalidAccessTokenError:
        return invalid_session_response()
    if claims.user.role != "cliente" or claims.scope != "cliente":
        return problem_response(403, "alcance_insuficiente", "Acceso denegado", "Requiere cliente.")
    return claims.user.user_id


def _gateway() -> PolicyModificationGateway:
    return cast(PolicyModificationGateway, current_app.extensions["policy_modification_gateway"])


def _uuid(value: str) -> UUID | None:
    try:
        return UUID(value)
    except ValueError:
        return None


def _too_large() -> Response | None:
    if request.headers.get("Transfer-Encoding"):
        return problem_response(411, "longitud_requerida", "Longitud requerida", "Indica longitud.")
    length = request.content_length
    if length is not None and length > MAX_BODY_BYTES:
        return problem_response(
            413, "limite_tamano", "Limite excedido", "El cuerpo excede el tamano permitido."
        )
    return None


def _invalid(detail: str) -> Response:
    return problem_response(400, "solicitud_invalida", "Solicitud invalida", detail)


def _not_found() -> Response:
    return problem_response(
        404,
        "poliza_no_encontrada",
        "Poliza no encontrada",
        "La poliza no existe o no esta disponible para tu cuenta.",
    )


def _unavailable(detail: str, action: str) -> Response:
    response = problem_response(
        503,
        "servicio_no_disponible",
        "Servicio no disponible",
        detail,
        extensions={"accion": action},
    )
    response.headers["Retry-After"] = "5"
    return response
