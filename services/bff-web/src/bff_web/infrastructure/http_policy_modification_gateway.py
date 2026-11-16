from typing import Any
from urllib.error import HTTPError
from uuid import UUID

from pydantic import ValidationError

from bff_web.domain.policy_modification import (
    ModificationConfirmation,
    ModificationConflictError,
    ModificationImpact,
    ModificationRejectedError,
    ModificationRequest,
    ModificationResult,
    ModificationState,
    PolicyNotModifiableError,
)
from bff_web.domain.policy_renewal import PolicyNotFoundError, RenewalServiceUnavailableError
from bff_web.infrastructure.http_policy_renewal_gateway import HttpPolicyRenewalGateway

NOT_MODIFIABLE = {
    "producto_no_modificable",
    "poliza_cancelada",
    "poliza_programada",
    "poliza_vencida",
    "fin_de_vigencia",
}


class HttpPolicyModificationGateway(HttpPolicyRenewalGateway):
    """Reutiliza el transporte autenticado hacia Polizas con su propio contrato de errores."""

    def get_state(self, policy_id: UUID, client_id: UUID, correlation_id: str) -> ModificationState:
        payload, _ = self._call(
            "GET", f"/v1/polizas/{policy_id}/modificacion", client_id, correlation_id
        )
        state = _validate(ModificationState, payload)
        if state.policy_id != policy_id:
            raise RenewalServiceUnavailableError()
        return state

    def simulate(
        self,
        policy_id: UUID,
        client_id: UUID,
        request: ModificationRequest,
        correlation_id: str,
    ) -> ModificationImpact:
        payload, _ = self._call(
            "POST",
            f"/v1/polizas/{policy_id}/modificaciones/simulacion",
            client_id,
            correlation_id,
            body=request.model_dump(mode="json", by_alias=True),
        )
        impact = _validate(ModificationImpact, payload)
        if impact.base_version != request.base_version or impact.plan.new != request.plan:
            raise RenewalServiceUnavailableError()
        return impact

    def confirm(
        self,
        policy_id: UUID,
        client_id: UUID,
        idempotency_key: UUID,
        confirmation: ModificationConfirmation,
        correlation_id: str,
    ) -> tuple[ModificationResult, int]:
        payload, status = self._call(
            "POST",
            f"/v1/polizas/{policy_id}/modificaciones",
            client_id,
            correlation_id,
            body=confirmation.model_dump(mode="json", by_alias=True),
            idempotency_key=idempotency_key,
        )
        result = _validate(ModificationResult, payload)
        if (
            status not in {200, 201}
            or result.policy_id != policy_id
            or result.impact.impact_hash != confirmation.impact_hash
            or result.previous_version != confirmation.base_version
        ):
            raise RenewalServiceUnavailableError()
        return result, status

    def _translate_error(self, error: HTTPError, payload: dict[str, Any]) -> None:
        code = payload.get("error")
        if error.code == 404 and code == "poliza_no_encontrada":
            raise PolicyNotFoundError() from error
        if error.code == 422 and code == "poliza_no_modificable":
            reason = payload.get("motivo")
            if reason in NOT_MODIFIABLE:
                raise PolicyNotModifiableError(reason) from error
        if error.code == 422 and code in {"cambio_no_permitido", "sin_cambios"}:
            field = payload.get("campo")
            raise ModificationRejectedError(
                str(code), field if isinstance(field, str) else None
            ) from error
        if error.code == 409 and code in {"impacto_cambio", "idempotencia_conflictiva"}:
            raise ModificationConflictError(str(code)) from error
        if error.code == 409 and code == "version_desactualizada":
            current = payload.get("versionActual")
            if isinstance(current, int) and not isinstance(current, bool) and current >= 1:
                raise ModificationConflictError(str(code), current) from error


def _validate[T: (ModificationState, ModificationImpact, ModificationResult)](
    model: type[T], payload: object
) -> T:
    try:
        return model.model_validate(payload)
    except ValidationError as error:
        raise RenewalServiceUnavailableError() from error
