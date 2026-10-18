import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from uuid import UUID

from bff_web.domain.profiling import PROFILING_ERRORS, ProfilingError


class HttpProfilingGateway:
    def __init__(self, identity_url: str, quote_url: str, token: str) -> None:
        self._identity_url = identity_url.rstrip("/")
        self._quote_url = quote_url.rstrip("/")
        self._token = token

    def get_consent(self, user_id: UUID, correlation_id: str) -> dict[str, object]:
        return self._request(
            f"{self._identity_url}/v1/clientes/{user_id}/consentimiento-perfilamiento",
            correlation_id,
        )

    def set_consent(
        self, user_id: UUID, authorized: bool, version: int, correlation_id: str
    ) -> dict[str, object]:
        return self._request(
            f"{self._identity_url}/v1/clientes/{user_id}/consentimiento-perfilamiento",
            correlation_id,
            {"autorizado": authorized, "version": version},
        )

    def get_profile(self, user_id: UUID, request_id: str, correlation_id: str) -> dict[str, object]:
        query = urlencode({"clienteId": str(user_id)})
        return self._request(
            f"{self._quote_url}/v1/solicitudes-cotizacion/"
            f"{quote(request_id, safe='')}/perfilamiento?{query}",
            correlation_id,
        )

    def _request(
        self, url: str, correlation_id: str, body: dict[str, object] | None = None
    ) -> dict[str, object]:
        if not self._token.strip():
            raise ProfilingError("perfilamiento_no_disponible", 503)
        request = Request(
            url,
            method="PUT" if body is not None else "GET",
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Authorization": f"Bearer {self._token}",
                "X-Correlation-ID": correlation_id,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=5) as response:
                payload = json.load(response)
        except HTTPError as error:
            try:
                problem = json.load(error)
            except (ValueError, OSError):
                raise ProfilingError("perfilamiento_no_disponible", 503) from error
            code = problem.get("code", problem.get("error")) if isinstance(problem, dict) else None
            if error.code == 404 and code == "consentimiento_ausente":
                raise ProfilingError(code, 404) from error
            if error.code == 404 and code == "no_encontrado":
                raise ProfilingError("solicitud_no_encontrada", 404) from error
            if isinstance(code, str) and PROFILING_ERRORS.get(code) == error.code:
                raise ProfilingError(code, error.code) from error
            raise ProfilingError("perfilamiento_no_disponible", 503) from error
        except (URLError, OSError, ValueError) as error:
            raise ProfilingError("perfilamiento_no_disponible", 503) from error
        if not isinstance(payload, dict):
            raise ProfilingError("perfilamiento_no_disponible", 503)
        return {str(key): value for key, value in payload.items()}
