import json
from http.client import HTTPException
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import UUID

from cotizacion.domain.errors import ServicioPolizasNoDisponibleError


class PolizasHttp:
    def __init__(self, url: str, token: str, timeout: float = 3) -> None:
        self.url = url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _consultar(
        self, ruta: str, correlation_id: str, evidencia: dict[str, object] | None = None
    ) -> dict[str, object]:
        peticion = Request(
            self.url + ruta,
            data=json.dumps(evidencia).encode() if evidencia is not None else None,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "X-Correlation-ID": correlation_id,
            },
        )
        try:
            with urlopen(peticion, timeout=self.timeout) as respuesta:
                resultado = json.load(respuesta)
            if not isinstance(resultado, dict):
                raise ValueError("Respuesta de polizas invalida.")
            return resultado
        except (OSError, HTTPException, ValueError) as error:
            raise ServicioPolizasNoDisponibleError() from error

    def emitir(self, evidencia: dict[str, object], correlation_id: str) -> UUID:
        resultado = self._consultar("/v1/polizas", correlation_id, evidencia)
        try:
            if (
                resultado["suscripcionId"] != evidencia["suscripcionId"]
                or resultado["simulada"] is not True
                or resultado["tipo"] != evidencia["tipo"]
            ):
                raise ValueError("La poliza no corresponde al pago.")
            return UUID(str(resultado["id"]))
        except (KeyError, ValueError, TypeError) as error:
            raise ServicioPolizasNoDisponibleError() from error

    def listar(self, cliente_id: UUID, correlation_id: str) -> list[object]:
        resultado = self._consultar(
            "/v1/polizas?" + urlencode({"clienteId": str(cliente_id)}), correlation_id
        )
        polizas = resultado.get("polizas")
        if not isinstance(polizas, list):
            raise ServicioPolizasNoDisponibleError()
        return polizas
