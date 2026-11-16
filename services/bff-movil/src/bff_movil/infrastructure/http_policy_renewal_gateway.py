import json
from collections.abc import Callable
from http.client import HTTPException
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID

from pydantic import BaseModel, ValidationError

from bff_movil.domain import policy_modification as modification
from bff_movil.domain.policy_modification import (
    ModificationConfirmation,
    ModificationConflictError,
    ModificationImpact,
    ModificationRejectedError,
    ModificationRequest,
    ModificationResult,
    ModificationState,
    PolicyNotModifiableError,
    PolicyServiceUnavailableError,
)
from bff_movil.domain.policy_renewal import (
    NON_RENEWABLE_REASONS,
    PolicyNotFoundError,
    PolicyNotRenewableError,
    Renewal,
    RenewalConflictError,
    RenewalEligibility,
    RenewalRequest,
    RenewalServiceUnavailableError,
)

CONFLICTS = {"condiciones_cambiaron", "idempotencia_conflictiva", "renovacion_en_curso"}
NOT_MODIFIABLE = {
    "producto_no_modificable",
    "poliza_programada",
    "poliza_vencida",
    "fin_de_vigencia",
}
MAX_PAYLOAD = 256 * 1024

ErrorTranslator = Callable[[HTTPError, dict[str, Any]], None]


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


_OPENER = build_opener(_NoRedirect)


def urlopen(request: Request, timeout: float) -> Any:
    """Abre sin seguir redirecciones para no reenviar el token interno a otro destino."""
    return _OPENER.open(request, timeout=timeout)


class HttpPolicyRenewalGateway:
    """Cliente unico del servicio central de Polizas: el BFF movil no evalua reglas."""

    def __init__(
        self,
        base_url: str,
        internal_api_token: str,
        query_timeout: float = 0.8,
        renewal_timeout: float = 5,
        modification_query_timeout: float = 2,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = internal_api_token
        self._query_timeout = query_timeout
        self._renewal_timeout = renewal_timeout
        self._modification_query_timeout = modification_query_timeout

    def get_eligibility(
        self, policy_id: UUID, client_id: UUID, correlation_id: str
    ) -> RenewalEligibility:
        payload, status = self._call(
            "GET", f"/v1/polizas/{policy_id}/renovacion", client_id, correlation_id
        )
        try:
            eligibility = RenewalEligibility.model_validate(payload)
        except ValidationError as error:
            raise RenewalServiceUnavailableError() from error
        if status != 200 or eligibility.policy_id != policy_id:
            raise RenewalServiceUnavailableError()
        return eligibility

    def renew(
        self,
        policy_id: UUID,
        client_id: UUID,
        idempotency_key: UUID,
        request: RenewalRequest,
        correlation_id: str,
    ) -> tuple[Renewal, int]:
        payload, status = self._call(
            "POST",
            f"/v1/polizas/{policy_id}/renovaciones",
            client_id,
            correlation_id,
            body=request.model_dump(mode="json", by_alias=True),
            idempotency_key=idempotency_key,
        )
        try:
            renewal = Renewal.model_validate(payload)
        except ValidationError as error:
            raise RenewalServiceUnavailableError() from error
        if (
            renewal.policy_id != policy_id
            or renewal.conditions_hash != request.conditions_hash
            or status not in {200, 201, 202}
            or (status == 202) != (renewal.state == "pendiente")
        ):
            raise RenewalServiceUnavailableError()
        return renewal, status

    def list_policies(self, client_id: UUID, correlation_id: str) -> dict[str, Any]:
        return self._query("/v1/clientes/me/polizas", client_id, correlation_id)

    def get_policy(self, policy_id: UUID, client_id: UUID, correlation_id: str) -> dict[str, Any]:
        return self._query(f"/v1/clientes/me/polizas/{policy_id}", client_id, correlation_id)

    def get_state(self, policy_id: UUID, client_id: UUID, correlation_id: str) -> ModificationState:
        payload, _ = self._modification_call(
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
        payload, _ = self._modification_call(
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
        payload, status = self._modification_call(
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

    def _query(self, path: str, client_id: UUID, correlation_id: str) -> dict[str, Any]:
        try:
            payload, status = self._call("GET", path, client_id, correlation_id)
        except (RenewalConflictError, PolicyNotRenewableError) as error:
            raise RenewalServiceUnavailableError() from error
        if status != 200 or not isinstance(payload, dict):
            raise RenewalServiceUnavailableError()
        return payload

    def _modification_call(
        self,
        method: str,
        path: str,
        client_id: UUID,
        correlation_id: str,
        body: dict[str, Any] | None = None,
        idempotency_key: UUID | None = None,
    ) -> tuple[object, int]:
        return self._call(
            method,
            path,
            client_id,
            correlation_id,
            body,
            idempotency_key,
            translate=_raise_modification_error,
            unavailable=PolicyServiceUnavailableError,
            query_timeout=self._modification_query_timeout,
        )

    def _call(
        self,
        method: str,
        path: str,
        client_id: UUID,
        correlation_id: str,
        body: dict[str, Any] | None = None,
        idempotency_key: UUID | None = None,
        *,
        translate: ErrorTranslator | None = None,
        unavailable: type[Exception] = RenewalServiceUnavailableError,
        query_timeout: float | None = None,
    ) -> tuple[object, int]:
        if not self._base_url or not self._token:
            raise unavailable()
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
        if method == "GET":
            timeout = self._query_timeout if query_timeout is None else query_timeout
        else:
            timeout = self._renewal_timeout
        try:
            with urlopen(request, timeout=timeout) as response:
                return _read(response), response.status
        except HTTPError as error:
            payload = _error_payload(error)
            if payload is not None:
                (translate or _raise_renewal_error)(error, payload)
            raise unavailable() from error
        except (URLError, OSError, HTTPException, ValueError) as error:
            raise unavailable() from error


def _read(response: Any) -> object:
    raw = response.read(MAX_PAYLOAD + 1)
    if len(raw) > MAX_PAYLOAD:
        raise ValueError("Respuesta de polizas demasiado grande.")
    return json.loads(raw)


def _error_payload(error: HTTPError) -> dict[str, Any] | None:
    try:
        with error:
            payload = _read(error)
    except (ValueError, OSError, HTTPException):
        return None
    return payload if isinstance(payload, dict) else None


def _raise_renewal_error(error: HTTPError, payload: dict[str, Any]) -> None:
    code = payload.get("error")
    if error.code == 404 and code == "poliza_no_encontrada":
        raise PolicyNotFoundError() from error
    if error.code == 422 and code == "poliza_no_renovable":
        reason = payload.get("motivo")
        if reason in NON_RENEWABLE_REASONS:
            raise PolicyNotRenewableError(reason) from error
    if error.code == 409 and code in CONFLICTS:
        renewal_id = payload.get("renovacionId")
        try:
            parsed = UUID(renewal_id) if isinstance(renewal_id, str) else None
        except ValueError:
            return
        raise RenewalConflictError(str(code), parsed) from error


def _raise_modification_error(error: HTTPError, payload: dict[str, Any]) -> None:
    code = payload.get("error")
    if error.code == 404 and code == "poliza_no_encontrada":
        raise modification.PolicyNotFoundError() from error
    if error.code == 422 and code == "poliza_no_modificable":
        reason = payload.get("motivo")
        if reason in NOT_MODIFIABLE:
            raise PolicyNotModifiableError(reason) from error
    if error.code == 422 and code == "cambio_no_permitido":
        field = payload.get("campo")
        raise ModificationRejectedError(code, field if isinstance(field, str) else None) from error
    if error.code == 422 and code == "sin_cambios":
        raise ModificationRejectedError(code) from error
    if error.code == 409 and code in {"impacto_cambio", "idempotencia_conflictiva"}:
        raise ModificationConflictError(code) from error
    if error.code == 409 and code == "version_desactualizada":
        current = payload.get("versionActual")
        if isinstance(current, int) and not isinstance(current, bool) and current >= 1:
            raise ModificationConflictError(code, current) from error


def _validate[T: BaseModel](model: type[T], payload: object) -> T:
    try:
        return model.model_validate(payload)
    except ValidationError as error:
        raise PolicyServiceUnavailableError() from error
