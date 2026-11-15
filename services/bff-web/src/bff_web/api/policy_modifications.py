from typing import cast

from flask import Blueprint, Response, current_app, g, jsonify, request
from pydantic import ValidationError

from bff_web.api.policy_renewals import (
    _client,
    _invalid,
    _not_found,
    _unavailable,
    _uuid,
    private,
)
from bff_web.application.policy_renewal_ports import PolicyModificationGateway
from bff_web.domain.policy_modification import (
    ModificationConfirmation,
    ModificationConflictError,
    ModificationRejectedError,
    ModificationRequest,
    PolicyNotModifiableError,
)
from bff_web.domain.policy_renewal import PolicyNotFoundError, RenewalServiceUnavailableError
from bff_web.problems import problem_response

bp = Blueprint("policy_modifications", __name__, url_prefix="/v1/clientes/me/polizas")
bp.after_request(private)

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
    except RenewalServiceUnavailableError:
        return _unavailable("No fue posible consultar las condiciones de la poliza.")
    return jsonify(state.model_dump(mode="json", by_alias=True))


@bp.post("/<policy_id>/modificaciones/simulacion")
def simulate(policy_id: str) -> Response:
    client_id = _client()
    if isinstance(client_id, Response):
        return client_id
    policy = _uuid(policy_id)
    if policy is None or request.args:
        return _invalid("La poliza se identifica con un UUID y no admite parametros.")
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
    except RenewalServiceUnavailableError:
        return _unavailable("No fue posible calcular el impacto de la modificacion.")
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
    try:
        confirmation = ModificationConfirmation.model_validate(request.get_json(silent=True))
    except ValidationError:
        return _invalid("Debes confirmar explicitamente el impacto simulado.")
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
    except RenewalServiceUnavailableError:
        return _unavailable(
            "No se confirmo la modificacion. Reintenta con la misma clave para conocer "
            "el resultado."
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
        return problem_response(
            422,
            error.code,
            "Modificacion rechazada",
            REJECTION_DETAILS[error.code],
            extensions={"campo": error.field} if error.field else None,
        )
    if isinstance(error, ModificationConflictError):
        return problem_response(
            409,
            error.code,
            "Conflicto de modificacion",
            CONFLICT_DETAILS[error.code],
            extensions=(
                {"versionActual": error.current_version}
                if error.current_version is not None
                else None
            ),
        )
    return _not_found()


def _gateway() -> PolicyModificationGateway:
    return cast(PolicyModificationGateway, current_app.extensions["policy_modification_gateway"])
