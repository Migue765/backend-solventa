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
from bff_web.application.policy_renewal_ports import PolicyCancellationGateway
from bff_web.domain.policy_cancellation import (
    CancellationConfirmation,
    CancellationConflictError,
    PolicyNotCancellableError,
)
from bff_web.domain.policy_renewal import PolicyNotFoundError, RenewalServiceUnavailableError
from bff_web.problems import problem_response

bp = Blueprint("policy_cancellations", __name__, url_prefix="/v1/clientes/me/polizas")
bp.after_request(private)

NOT_CANCELLABLE_DETAILS = {
    "producto_no_cancelable": "Este producto se contrata por fechas y no admite cancelacion.",
    "poliza_cancelada": "La poliza ya fue cancelada.",
    "poliza_programada": "La poliza aun no inicia su vigencia.",
    "poliza_vencida": "La poliza ya vencio y no requiere cancelacion.",
    "fin_de_vigencia": "La poliza termina hoy; no hay cobertura restante por cancelar.",
    "renovacion_en_curso": "Hay una renovacion en proceso; espera su resultado para cancelar.",
}
CONFLICT_DETAILS = {
    "liquidacion_cambio": "La liquidacion cambio; consultala nuevamente antes de confirmar.",
    "idempotencia_conflictiva": "La clave de la operacion ya se uso con otra solicitud.",
}


@bp.get("/<policy_id>/cancelacion")
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
        return _unavailable("No fue posible consultar la cancelacion de la poliza.")
    return jsonify(state.model_dump(mode="json", by_alias=True))


@bp.post("/<policy_id>/cancelaciones")
def cancel(policy_id: str) -> Response:
    client_id = _client()
    if isinstance(client_id, Response):
        return client_id
    policy = _uuid(policy_id)
    key = _uuid(request.headers.get("Idempotency-Key", ""))
    if policy is None or key is None or request.args:
        return _invalid("Indica la poliza y una Idempotency-Key UUID.")
    try:
        confirmation = CancellationConfirmation.model_validate(request.get_json(silent=True))
    except ValidationError:
        return _invalid("Debes confirmar explicitamente la cancelacion y su liquidacion.")
    try:
        cancellation, status = _gateway().cancel(
            policy, client_id, key, confirmation, str(g.correlation_id)
        )
    except PolicyNotFoundError:
        return _not_found()
    except PolicyNotCancellableError as error:
        return problem_response(
            422,
            "poliza_no_cancelable",
            "Poliza no cancelable",
            NOT_CANCELLABLE_DETAILS[error.reason],
            extensions={"motivo": error.reason},
        )
    except CancellationConflictError as error:
        return problem_response(
            409, error.code, "Conflicto de cancelacion", CONFLICT_DETAILS[error.code]
        )
    except RenewalServiceUnavailableError:
        return _unavailable(
            "No se confirmo la cancelacion. Reintenta con la misma clave para conocer el resultado."
        )
    response = jsonify(cancellation.model_dump(mode="json", by_alias=True))
    response.status_code = status
    return response


def _gateway() -> PolicyCancellationGateway:
    return cast(PolicyCancellationGateway, current_app.extensions["policy_cancellation_gateway"])
