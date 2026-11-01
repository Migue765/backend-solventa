import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import UUID

from bff_web.domain.open_finance_exceptions import OpenFinanceExceptionError


class HttpOpenFinanceExceptionGateway:
    def __init__(self, base_url: str, token: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token

    def list_pending(self, partner_id: str, correlation_id: str) -> dict[str, object]:
        query = urlencode({"socioId": partner_id})
        return self._request("GET", f"/v1/excepciones-open-finance?{query}", correlation_id)

    def get(self, exception_id: UUID, partner_id: str, correlation_id: str) -> dict[str, object]:
        query = urlencode({"socioId": partner_id})
        return self._request(
            "GET", f"/v1/excepciones-open-finance/{exception_id}?{query}", correlation_id
        )

    def resolve(
        self,
        exception_id: UUID,
        partner_id: str,
        actor_id: UUID,
        decision: str,
        reason: str,
        correlation_id: str,
    ) -> dict[str, object]:
        query = urlencode({"socioId": partner_id})
        return self._request(
            "POST",
            f"/v1/excepciones-open-finance/{exception_id}/resolver?{query}",
            correlation_id,
            {"decision": decision, "motivo": reason},
            str(actor_id),
        )

    def _request(
        self,
        method: str,
        path: str,
        correlation_id: str,
        body: dict[str, object] | None = None,
        actor_id: str | None = None,
    ) -> dict[str, object]:
        if not self._token.strip():
            raise OpenFinanceExceptionError("servicio_no_disponible", 503)
        headers = {
            "Authorization": f"Bearer {self._token}",
            "X-Correlation-ID": correlation_id,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if actor_id is not None:
            headers["X-Actor-ID"] = actor_id
        request = Request(
            self._base_url + path,
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers=headers,
        )
        try:
            with urlopen(request, timeout=5) as response:
                payload = json.load(response)
        except HTTPError as error:
            try:
                problem = json.load(error)
            except (ValueError, OSError):
                raise OpenFinanceExceptionError("servicio_no_disponible", 503) from error
            code = problem.get("error") if isinstance(problem, dict) else None
            allowed = {
                "solicitud_invalida": 400,
                "consentimiento_ausente": 403,
                "consentimiento_no_vigente": 403,
                "excepcion_no_encontrada": 404,
                "conflicto_idempotencia": 409,
                "excepcion_ya_resuelta": 409,
                "excepcion_no_integra": 503,
            }
            if isinstance(code, str) and allowed.get(code) == error.code:
                raise OpenFinanceExceptionError(code, error.code) from error
            raise OpenFinanceExceptionError("servicio_no_disponible", 503) from error
        except (URLError, OSError, ValueError) as error:
            raise OpenFinanceExceptionError("servicio_no_disponible", 503) from error
        if not isinstance(payload, dict):
            raise OpenFinanceExceptionError("servicio_no_disponible", 503)
        return {str(key): value for key, value in payload.items()}
