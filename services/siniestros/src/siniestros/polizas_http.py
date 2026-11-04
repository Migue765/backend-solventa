import json
from http.client import HTTPException
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import UUID

from siniestros.domain import ErrorSiniestro, PolizaAutorizada, PolizasAutorizadas


class PolizasHttp:
    def __init__(self, url: str, token: str, timeout: float = 3) -> None:
        self.url = url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def listar(self, cliente_id: UUID, correlation_id: str) -> list[PolizaAutorizada]:
        peticion = Request(
            self.url + "/v1/polizas?" + urlencode({"clienteId": str(cliente_id)}),
            headers={
                "Authorization": f"Bearer {self.token}",
                "X-Correlation-ID": correlation_id,
            },
        )
        try:
            with urlopen(peticion, timeout=self.timeout) as respuesta:
                return PolizasAutorizadas.model_validate(json.load(respuesta)).polizas
        except (OSError, HTTPException, ValueError, TypeError) as error:
            raise ErrorSiniestro("servicio_no_disponible", 503) from error
