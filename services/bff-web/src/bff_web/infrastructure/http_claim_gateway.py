import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import UUID

from pydantic import ValidationError

from bff_web.domain.claims import ClaimNotFoundError, ClaimServiceUnavailableError, ClaimStatus


class HttpClaimGateway:
    def __init__(self, base_url: str, internal_api_token: str, timeout: float = 0.8) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = internal_api_token
        self._timeout = timeout

    def get_status(self, claim_id: UUID, client_id: UUID, correlation_id: str) -> ClaimStatus:
        if not self._token:
            raise ClaimServiceUnavailableError()
        request = Request(
            f"{self._base_url}/v1/siniestros/{claim_id}?"
            + urlencode({"clienteId": str(client_id)}),
            headers={
                "Authorization": "Bearer " + self._token,
                "Accept": "application/json",
                "X-Correlation-ID": correlation_id,
            },
        )
        try:
            with urlopen(request, timeout=self._timeout) as response:  # noqa: S310
                status = ClaimStatus.model_validate(json.load(response))
        except HTTPError as error:
            if error.code == 404 and _error_code(error) == "siniestro_no_encontrado":
                raise ClaimNotFoundError() from error
            raise ClaimServiceUnavailableError() from error
        except (URLError, OSError, TimeoutError, ValueError, ValidationError) as error:
            raise ClaimServiceUnavailableError() from error
        if status.claim_id != claim_id:
            raise ClaimServiceUnavailableError()
        return status


def _error_code(error: HTTPError) -> object:
    try:
        payload = json.load(error)
    except ValueError:
        return None
    return payload.get("error") if isinstance(payload, dict) else None
