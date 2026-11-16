from typing import Any
from urllib.error import HTTPError
from uuid import UUID

from pydantic import ValidationError

from bff_web.domain.policy_cancellation import (
    Cancellation,
    CancellationConfirmation,
    CancellationConflictError,
    CancellationState,
    PolicyNotCancellableError,
)
from bff_web.domain.policy_renewal import PolicyNotFoundError, RenewalServiceUnavailableError
from bff_web.infrastructure.http_policy_renewal_gateway import HttpPolicyRenewalGateway

NOT_CANCELLABLE = {
    "producto_no_cancelable",
    "poliza_cancelada",
    "poliza_programada",
    "poliza_vencida",
    "fin_de_vigencia",
    "renovacion_en_curso",
}
CONFLICTS = {"liquidacion_cambio", "idempotencia_conflictiva"}


class HttpPolicyCancellationGateway(HttpPolicyRenewalGateway):
    """Reutiliza el transporte autenticado hacia Polizas con su propio contrato de errores."""

    def get_state(self, policy_id: UUID, client_id: UUID, correlation_id: str) -> CancellationState:
        payload, _ = self._call(
            "GET", f"/v1/polizas/{policy_id}/cancelacion", client_id, correlation_id
        )
        state = _validate(CancellationState, payload)
        if state.policy_id != policy_id:
            raise RenewalServiceUnavailableError()
        return state

    def cancel(
        self,
        policy_id: UUID,
        client_id: UUID,
        idempotency_key: UUID,
        confirmation: CancellationConfirmation,
        correlation_id: str,
    ) -> tuple[Cancellation, int]:
        payload, status = self._call(
            "POST",
            f"/v1/polizas/{policy_id}/cancelaciones",
            client_id,
            correlation_id,
            body=confirmation.model_dump(mode="json", by_alias=True),
            idempotency_key=idempotency_key,
        )
        cancellation = _validate(Cancellation, payload)
        if (
            status not in {200, 201}
            or cancellation.policy_id != policy_id
            or cancellation.settlement.settlement_hash != confirmation.settlement_hash
            or cancellation.reason != confirmation.reason
        ):
            raise RenewalServiceUnavailableError()
        return cancellation, status

    def _translate_error(self, error: HTTPError, payload: dict[str, Any]) -> None:
        code = payload.get("error")
        if error.code == 404 and code == "poliza_no_encontrada":
            raise PolicyNotFoundError() from error
        if error.code == 422 and code == "poliza_no_cancelable":
            reason = payload.get("motivo")
            if reason in NOT_CANCELLABLE:
                raise PolicyNotCancellableError(reason) from error
        if error.code == 409 and code in CONFLICTS:
            raise CancellationConflictError(str(code)) from error


def _validate[T: (CancellationState, Cancellation)](model: type[T], payload: object) -> T:
    try:
        return model.model_validate(payload)
    except ValidationError as error:
        raise RenewalServiceUnavailableError() from error
