import json
from typing import TypeVar
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import UUID

from pydantic import BaseModel

from bff_web.domain.mortgage_profiling import MortgageProfilingError
from bff_web.domain.partner_offer import PartnerOffer
from bff_web.domain.partner_operation import (
    PartnerClients,
    PartnerOperation,
    PartnerProvisionalSelection,
    PartnerSelectionState,
)
from bff_web.domain.quote_history import QuoteHistory
from bff_web.infrastructure.http_mortgage_profiling_gateway import _controlled_error

_Model = TypeVar("_Model", bound=BaseModel)

OFFER_ERRORS = {
    404: {"operacion_no_encontrada", "oferta_no_encontrada", "no_encontrado"},
    409: {"conflicto_idempotencia", "seleccion_desactualizada", "seleccion_no_modificable"},
    422: {
        "oferta_no_disponible",
        "cotizacion_vencida",
        "perfil_vencido",
        "fuente_vencida",
        "decision_inconsistente",
        "decision_sin_tarificacion",
        "cotizacion_no_disponible",
        "modelo_no_soportado",
    },
}


class HttpPartnerQuoteGateway:
    def __init__(self, base_url: str, internal_api_token: str, timeout: float = 0.8) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = internal_api_token
        self._timeout = timeout

    def start(
        self, payload: dict[str, object], operation_id: UUID, correlation_id: str
    ) -> PartnerOperation:
        return self._request("", operation_id, correlation_id, PartnerOperation, payload)

    def get(self, socio_id: str, operation_id: UUID, correlation_id: str) -> PartnerOperation:
        return self._request(
            f"/{operation_id}?{urlencode({'socioId': socio_id})}",
            operation_id,
            correlation_id,
            PartnerOperation,
        )

    def list_clients(self, socio_id: str, product_id: str, correlation_id: str) -> PartnerClients:
        return self._request(
            f"?{urlencode({'socioId': socio_id, 'productoId': product_id})}",
            None,
            correlation_id,
            PartnerClients,
            resource="/v1/socios/clientes",
        )

    def list_requests(self, socio_id: str, product_id: str, correlation_id: str) -> QuoteHistory:
        return self._request(
            f"?{urlencode({'socioId': socio_id, 'productoId': product_id})}",
            None,
            correlation_id,
            QuoteHistory,
        )

    def calculate_offer(
        self, socio_id: str, operation_id: UUID, correlation_id: str
    ) -> PartnerOffer:
        return self._request(
            f"/{operation_id}/oferta?{urlencode({'socioId': socio_id})}",
            operation_id,
            correlation_id,
            PartnerOffer,
            {},
        )

    def get_offer(self, socio_id: str, operation_id: UUID, correlation_id: str) -> PartnerOffer:
        return self._request(
            f"/{operation_id}/oferta?{urlencode({'socioId': socio_id})}",
            operation_id,
            correlation_id,
            PartnerOffer,
        )

    def get_selection(
        self, socio_id: str, operation_id: UUID, correlation_id: str
    ) -> PartnerSelectionState:
        return self._request(
            f"/{operation_id}/seleccion-provisional?{urlencode({'socioId': socio_id})}",
            None,
            correlation_id,
            PartnerSelectionState,
        )

    def select_offer(
        self,
        socio_id: str,
        operation_id: UUID,
        payload: dict[str, object],
        key: UUID,
        correlation_id: str,
    ) -> PartnerProvisionalSelection:
        return self._request(
            f"/{operation_id}/seleccion-provisional?{urlencode({'socioId': socio_id})}",
            key,
            correlation_id,
            PartnerProvisionalSelection,
            payload,
            method="PUT",
        )

    def _request(
        self,
        suffix: str,
        operation_id: UUID | None,
        correlation_id: str,
        model: type[_Model],
        payload: dict[str, object] | None = None,
        *,
        resource: str = "/v1/socios/solicitudes-cotizacion",
        method: str | None = None,
    ) -> _Model:
        if not self._token:
            raise MortgageProfilingError("servicio_no_disponible", 503)
        request = Request(
            f"{self._base_url}{resource}{suffix}",
            data=json.dumps(payload).encode() if payload is not None else None,
            method=method or ("POST" if payload is not None else "GET"),
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-Correlation-ID": correlation_id,
                "API-Version": "1",
            },
        )
        if operation_id is not None:
            request.add_header("Idempotency-Key", str(operation_id))
        try:
            with urlopen(request, timeout=self._timeout) as response:  # noqa: S310
                return model.model_validate(json.load(response))
        except HTTPError as error:
            if model in (PartnerOffer, PartnerSelectionState, PartnerProvisionalSelection):
                raise _offer_error(error) from error
            raise _controlled_error(error) from error
        except (URLError, OSError, TimeoutError, ValueError) as error:
            raise MortgageProfilingError("servicio_no_disponible", 503) from error


def _offer_error(error: HTTPError) -> MortgageProfilingError:
    try:
        code = json.load(error)["error"]
    except (ValueError, KeyError, TypeError, OSError):
        return MortgageProfilingError("servicio_no_disponible", 503)
    if isinstance(code, str) and code in OFFER_ERRORS.get(error.code, set()):
        return MortgageProfilingError(code, error.code)
    return _controlled_error_from(error, code)


def _controlled_error_from(error: HTTPError, code: object) -> MortgageProfilingError:
    allowed = {
        400: {"solicitud_invalida"},
        403: {"consentimiento_ausente", "consentimiento_no_vigente"},
        503: {"perfilamiento_no_disponible", "servicio_no_disponible"},
    }
    if isinstance(code, str) and code in allowed.get(error.code, set()):
        return MortgageProfilingError(code, error.code)
    return MortgageProfilingError("servicio_no_disponible", 503)
