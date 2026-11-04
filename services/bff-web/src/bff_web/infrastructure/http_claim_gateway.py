import json
from http.client import HTTPException
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import UUID

from bff_web.domain.claims import ClaimDetail, ClaimDraft, ClaimError, ClaimList, ClaimPolicies

DOMAIN_ERRORS = {
    (400, "solicitud_invalida"),
    (404, "poliza_no_encontrada"),
    (404, "siniestro_no_encontrado"),
    (409, "conflicto_idempotencia"),
    (409, "poliza_no_vigente"),
}


class HttpClaimGateway:
    def __init__(self, base_url: str, token: str, timeout: float = 5) -> None:
        self.url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _request(
        self,
        path: str,
        correlation_id: str,
        body: dict[str, object] | None = None,
        key: UUID | None = None,
    ) -> tuple[object, int]:
        if not self.url or not self.token:
            raise ClaimError("servicio_no_disponible", 503)
        headers = {
            "Authorization": f"Bearer {self.token}",
            "X-Correlation-ID": correlation_id,
            "Content-Type": "application/json",
        }
        if key is not None:
            headers["Idempotency-Key"] = str(key)
        request = Request(
            self.url + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers=headers,
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return json.load(response), response.status
        except HTTPError as error:
            try:
                payload = json.load(error)
                code = payload.get("error") if isinstance(payload, dict) else None
            except (OSError, ValueError):
                code = None
            finally:
                error.close()
            if isinstance(code, str) and (error.code, code) in DOMAIN_ERRORS:
                raise ClaimError(code, error.code) from error
            raise ClaimError("servicio_no_disponible", 503) from error
        except (OSError, HTTPException, ValueError) as error:
            raise ClaimError("servicio_no_disponible", 503) from error

    def create(
        self, user_id: UUID, draft: ClaimDraft, key: UUID, correlation_id: str
    ) -> tuple[ClaimDetail, bool]:
        payload, status = self._request(
            "/v1/siniestros",
            correlation_id,
            {**draft.model_dump(mode="json", by_alias=True), "clienteId": str(user_id)},
            key,
        )
        try:
            result = ClaimDetail.model_validate(payload)
            if (
                status not in {200, 201}
                or result.policy_id != draft.policy_id
                or result.occurred_at != draft.occurred_at
                or result.event_type != draft.event_type
                or result.description != draft.description
                or result.location != draft.location
            ):
                raise ValueError("La confirmacion no corresponde a la poliza.")
            return result, status == 201
        except ValueError as error:
            raise ClaimError("servicio_no_disponible", 503) from error

    def list(self, user_id: UUID, correlation_id: str) -> ClaimList:
        payload, status = self._request(
            "/v1/siniestros?" + urlencode({"clienteId": str(user_id)}), correlation_id
        )
        try:
            if status != 200:
                raise ValueError("Estado de listado invalido.")
            return ClaimList.model_validate(payload)
        except ValueError as error:
            raise ClaimError("servicio_no_disponible", 503) from error

    def get(self, user_id: UUID, claim_id: UUID, correlation_id: str) -> ClaimDetail:
        payload, status = self._request(
            f"/v1/siniestros/{claim_id}?" + urlencode({"clienteId": str(user_id)}),
            correlation_id,
        )
        try:
            result = ClaimDetail.model_validate(payload)
            if status != 200 or result.id != claim_id:
                raise ValueError("La respuesta no corresponde al siniestro.")
            return result
        except ValueError as error:
            raise ClaimError("servicio_no_disponible", 503) from error

    def policies(self, user_id: UUID, correlation_id: str) -> ClaimPolicies:
        payload, status = self._request(
            "/v1/siniestros/polizas?" + urlencode({"clienteId": str(user_id)}), correlation_id
        )
        try:
            if status != 200:
                raise ValueError("Estado de consulta de polizas invalido.")
            return ClaimPolicies.model_validate(payload)
        except ValueError as error:
            raise ClaimError("servicio_no_disponible", 503) from error
