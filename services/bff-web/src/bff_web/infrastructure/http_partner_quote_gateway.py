import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import UUID

from bff_web.domain.mortgage_profiling import MortgageProfilingError
from bff_web.domain.partner_operation import PartnerOperation
from bff_web.infrastructure.http_mortgage_profiling_gateway import _controlled_error


class HttpPartnerQuoteGateway:
    def __init__(self, base_url: str, internal_api_token: str, timeout: float = 0.8) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = internal_api_token
        self._timeout = timeout

    def start(
        self, payload: dict[str, object], operation_id: UUID, correlation_id: str
    ) -> PartnerOperation:
        return self._request("", operation_id, correlation_id, payload)

    def get(self, socio_id: str, operation_id: UUID, correlation_id: str) -> PartnerOperation:
        return self._request(
            f"/{operation_id}?{urlencode({'socioId': socio_id})}", operation_id, correlation_id
        )

    def _request(
        self,
        suffix: str,
        operation_id: UUID,
        correlation_id: str,
        payload: dict[str, object] | None = None,
    ) -> PartnerOperation:
        if not self._token:
            raise MortgageProfilingError("servicio_no_disponible", 503)
        request = Request(
            f"{self._base_url}/v1/socios/solicitudes-cotizacion{suffix}",
            data=json.dumps(payload).encode() if payload is not None else None,
            method="POST" if payload is not None else "GET",
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-Correlation-ID": correlation_id,
                "Idempotency-Key": str(operation_id),
                "API-Version": "1",
            },
        )
        try:
            with urlopen(request, timeout=self._timeout) as response:  # noqa: S310
                return PartnerOperation.model_validate(json.load(response))
        except HTTPError as error:
            raise _controlled_error(error) from error
        except (URLError, OSError, TimeoutError, ValueError) as error:
            raise MortgageProfilingError("servicio_no_disponible", 503) from error
