import json
from http.client import HTTPException
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID

from pydantic import ValidationError

from bff_movil.domain.policy_modification import (
    ModificationConfirmation,
    ModificationConflictError,
    ModificationImpact,
    ModificationRejectedError,
    ModificationRequest,
    ModificationResult,
    ModificationState,
    PolicyNotFoundError,
    PolicyNotModifiableError,
    PolicyServiceUnavailableError,
)

MAX_RESPONSE_BYTES = 1024 * 1024
NOT_MODIFIABLE = {
    "producto_no_modificable",
    "poliza_programada",
    "poliza_vencida",
    "fin_de_vigencia",
}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


class HttpPolicyModificationGateway:
    """Consume la modificacion versionada central de Polizas (SOL-319) para el canal movil."""

    def __init__(
        self,
        base_url: str,
        internal_api_token: str,
        query_timeout: float = 2,
        command_timeout: float = 5,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = internal_api_token
        self._query_timeout = query_timeout
        self._command_timeout = command_timeout
        self._opener = build_opener(_NoRedirect)

    def get_state(self, policy_id: UUID, client_id: UUID, correlation_id: str) -> ModificationState:
        payload, _ = self._call(
            "GET", f"/v1/polizas/{policy_id}/modificacion", client_id, correlation_id
        )
        state = _validate(ModificationState, payload)
        if state.policy_id != policy_id:
            raise PolicyServiceUnavailableError()
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
            raise PolicyServiceUnavailableError()
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
            raise PolicyServiceUnavailableError()
        return result, status

    def _call(
        self,
        method: str,
        path: str,
        client_id: UUID,
        correlation_id: str,
        body: dict[str, Any] | None = None,
        idempotency_key: UUID | None = None,
    ) -> tuple[object, int]:
        if not self._base_url or not self._token:
            raise PolicyServiceUnavailableError()
        headers = {
            "Authorization": "Bearer " + self._token,
            "Accept": "application/json",
            "X-Correlation-ID": correlation_id,
            "X-Cliente-ID": str(client_id),
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        if idempotency_key is not None:
            headers["Idempotency-Key"] = str(idempotency_key)
        request = Request(
            self._base_url + path,
            method=method,
            headers=headers,
            data=json.dumps(body).encode() if body is not None else None,
        )
        timeout = self._query_timeout if method == "GET" else self._command_timeout
        try:
            with self._opener.open(request, timeout=timeout) as response:
                return _read(response), response.status
        except HTTPError as error:
            with error:
                payload = _problem(error)
            if payload is not None:
                _translate_error(error.code, payload)
            raise PolicyServiceUnavailableError() from error
        except (URLError, OSError, HTTPException, ValueError) as error:
            raise PolicyServiceUnavailableError() from error


def _read(response: Any) -> object:
    raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("Respuesta de Polizas demasiado grande.")
    return json.loads(raw)


def _problem(error: HTTPError) -> dict[str, Any] | None:
    try:
        payload = _read(error)
    except (ValueError, OSError, HTTPException):
        return None
    return payload if isinstance(payload, dict) else None


def _translate_error(status: int, payload: dict[str, Any]) -> None:
    code = payload.get("error")
    if status == 404 and code == "poliza_no_encontrada":
        raise PolicyNotFoundError()
    if status == 422 and code == "poliza_no_modificable":
        reason = payload.get("motivo")
        if reason in NOT_MODIFIABLE:
            raise PolicyNotModifiableError(reason)
    if status == 422 and code == "cambio_no_permitido":
        field = payload.get("campo")
        raise ModificationRejectedError(code, field if isinstance(field, str) else None)
    if status == 422 and code == "sin_cambios":
        raise ModificationRejectedError(code)
    if status == 409 and code in {"impacto_cambio", "idempotencia_conflictiva"}:
        raise ModificationConflictError(code)
    if status == 409 and code == "version_desactualizada":
        current = payload.get("versionActual")
        if isinstance(current, int) and not isinstance(current, bool) and current >= 1:
            raise ModificationConflictError(code, current)


def _validate[T: (ModificationState, ModificationImpact, ModificationResult)](
    model: type[T], payload: object
) -> T:
    try:
        return model.model_validate(payload)
    except ValidationError as error:
        raise PolicyServiceUnavailableError() from error
