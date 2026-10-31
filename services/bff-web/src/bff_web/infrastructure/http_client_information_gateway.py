import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import UUID

from pydantic import ValidationError

from bff_web.domain.client_information import (
    ClientInformationError,
    ClientInformationResult,
)


class HttpClientInformationGateway:
    def __init__(self, base_url: str, internal_api_token: str, timeout: float = 0.8) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = internal_api_token
        self._timeout = timeout

    def consult(self, payload: dict[str, object], correlation_id: str) -> ClientInformationResult:
        if not self._token:
            raise ClientInformationError("servicio_no_disponible", 503)
        request = Request(
            f"{self._base_url}/v1/consultas-informacion-cliente",
            data=json.dumps(payload).encode(),
            method="POST",
            headers={
                "Authorization": "Bearer " + self._token,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-Correlation-ID": correlation_id,
            },
        )
        return self._send(request)

    def recover(
        self, socio_id: str, operation_id: UUID, correlation_id: str
    ) -> ClientInformationResult:
        if not self._token:
            raise ClientInformationError("servicio_no_disponible", 503)
        request = Request(
            f"{self._base_url}/v1/consultas-informacion-cliente/{operation_id}?"
            + urlencode({"socioId": socio_id}),
            headers={
                "Authorization": "Bearer " + self._token,
                "Accept": "application/json",
                "X-Correlation-ID": correlation_id,
            },
        )
        return self._send(request)

    def _send(self, request: Request) -> ClientInformationResult:
        try:
            with urlopen(request, timeout=self._timeout) as response:  # noqa: S310
                return ClientInformationResult.model_validate(json.load(response))
        except HTTPError as error:
            raise _controlled_error(error) from error
        except (URLError, OSError, TimeoutError, ValueError, ValidationError) as error:
            raise ClientInformationError("servicio_no_disponible", 503) from error


def _controlled_error(error: HTTPError) -> ClientInformationError:
    try:
        payload = json.load(error)
        code = payload["error"]
    except (ValueError, KeyError, TypeError):
        return ClientInformationError("servicio_no_disponible", 503)
    allowed = {
        403: {
            "consentimiento_ausente",
            "consentimiento_no_vigente",
            "producto_no_autorizado",
            "finalidad_no_autorizada",
        },
        409: {"conflicto_idempotencia"},
        404: {"consulta_no_encontrada"},
        503: {
            "fuente_no_disponible",
            "circuito_abierto",
            "identidad_no_disponible",
            "servicio_no_disponible",
        },
    }
    if not isinstance(code, str) or code not in allowed.get(error.code, set()):
        return ClientInformationError("servicio_no_disponible", 503)
    return ClientInformationError(code, error.code)
