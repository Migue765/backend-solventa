import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import UUID

from pydantic import ValidationError

from bff_web.domain.policy_renewal import (
    PolicyNotFoundError,
    PolicyNotRenewableError,
    Renewal,
    RenewalConflictError,
    RenewalEligibility,
    RenewalRequest,
    RenewalServiceUnavailableError,
)

CONFLICTS = {"condiciones_cambiaron", "idempotencia_conflictiva", "renovacion_en_curso"}


class HttpPolicyRenewalGateway:
    def __init__(
        self,
        base_url: str,
        internal_api_token: str,
        query_timeout: float = 0.8,
        renewal_timeout: float = 5,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = internal_api_token
        self._query_timeout = query_timeout
        self._renewal_timeout = renewal_timeout

    def get_eligibility(
        self, policy_id: UUID, client_id: UUID, correlation_id: str
    ) -> RenewalEligibility:
        payload, _ = self._call(
            "GET", f"/v1/polizas/{policy_id}/renovacion", client_id, correlation_id
        )
        try:
            eligibility = RenewalEligibility.model_validate(payload)
        except ValidationError as error:
            raise RenewalServiceUnavailableError() from error
        if eligibility.policy_id != policy_id:
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
            raise RenewalServiceUnavailableError()
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
        timeout = self._query_timeout if method == "GET" else self._renewal_timeout
        try:
            with urlopen(request, timeout=timeout) as response:  # noqa: S310
                return json.load(response), response.status
        except HTTPError as error:
            _raise_business_error(error)
            raise RenewalServiceUnavailableError() from error
        except (URLError, OSError, TimeoutError, ValueError) as error:
            raise RenewalServiceUnavailableError() from error


def _raise_business_error(error: HTTPError) -> None:
    try:
        payload = json.load(error)
    except ValueError:
        return
    if not isinstance(payload, dict):
        return
    code = payload.get("error")
    if error.code == 404 and code == "poliza_no_encontrada":
        raise PolicyNotFoundError() from error
    if error.code == 422 and code == "poliza_no_renovable":
        reason = payload.get("motivo")
        if reason in {
            "producto_no_renovable",
            "poliza_programada",
            "poliza_vencida",
            "fuera_de_ventana",
        }:
            raise PolicyNotRenewableError(reason) from error
    if error.code == 409 and code in CONFLICTS:
        renewal_id = payload.get("renovacionId")
        try:
            parsed = UUID(renewal_id) if isinstance(renewal_id, str) else None
        except ValueError:
            return
        raise RenewalConflictError(str(code), parsed) from error
