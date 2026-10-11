import json
from datetime import datetime
from http import HTTPStatus
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import UUID

from bff_web.application.quote_ports import QuoteGateway
from bff_web.domain.quote_errors import (
    InsuranceProductUnavailableError,
    QuoteRequestConflictError,
    QuoteRequestNotFoundError,
    QuoteServiceUnavailableError,
)
from bff_web.domain.quotes import InsuranceProduct, QuoteRequestDraft, QuoteRequestRecord


class HttpQuoteGateway(QuoteGateway):
    def __init__(self, base_url: str, timeout_seconds: float = 5) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    def list_products(self) -> tuple[InsuranceProduct, ...]:
        payload = self._request_json("/v1/productos-seguro")
        try:
            return tuple(
                InsuranceProduct(
                    product_id=item["id"],
                    name=item["nombre"],
                    description=item["descripcion"],
                    available=item["disponible"],
                )
                for item in payload["productos"]
            )
        except (KeyError, TypeError) as error:
            raise QuoteServiceUnavailableError() from error

    def create_request(
        self,
        user_id: UUID,
        draft: QuoteRequestDraft,
        idempotency_key: UUID,
    ) -> QuoteRequestRecord:
        payload = self._request_json(
            "/v1/solicitudes-cotizacion",
            method="POST",
            headers={"Idempotency-Key": str(idempotency_key)},
            body={
                "clienteId": str(user_id),
                "productoId": draft.product_id,
                "tipoCliente": draft.customer_type,
                "identificacion": draft.identification,
                "patenteVehiculo": draft.vehicle_plate,
                "correo": draft.email,
                "telefono": draft.phone,
            },
        )
        return _safe_record_from_json(payload)

    def get_request(self, user_id: UUID, request_id: str) -> QuoteRequestRecord:
        query = urlencode({"clienteId": str(user_id)})
        payload = self._request_json(f"/v1/solicitudes-cotizacion/{request_id}?{query}")
        return _safe_record_from_json(payload)

    def _request_json(
        self,
        path: str,
        *,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        body: dict[str, object] | None = None,
    ) -> dict[str, Any]:
        request_headers = {"Accept": "application/json", **(headers or {})}
        encoded_body = None
        if body is not None:
            request_headers["Content-Type"] = "application/json"
            encoded_body = json.dumps(body).encode()
        request = Request(
            f"{self._base_url}{path}",
            data=encoded_body,
            headers=request_headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:
                payload: Any = json.load(response)
        except HTTPError as error:
            if error.code == HTTPStatus.NOT_FOUND:
                raise QuoteRequestNotFoundError() from error
            if error.code == HTTPStatus.CONFLICT:
                raise QuoteRequestConflictError() from error
            if error.code == HTTPStatus.UNPROCESSABLE_ENTITY:
                raise InsuranceProductUnavailableError() from error
            raise QuoteServiceUnavailableError() from error
        except (URLError, TimeoutError, json.JSONDecodeError) as error:
            raise QuoteServiceUnavailableError() from error

        if not isinstance(payload, dict):
            raise QuoteServiceUnavailableError()
        return payload


def _record_from_json(payload: dict[str, Any]) -> QuoteRequestRecord:
    draft = QuoteRequestDraft(
        product_id=payload["productoId"],
        customer_type=payload["tipoCliente"],
        identification=payload["identificacion"],
        vehicle_plate=payload["patenteVehiculo"],
        email=payload["correo"],
        phone=payload["telefono"],
    )
    return QuoteRequestRecord(
        request_id=payload["id"],
        draft=draft,
        status=payload["estado"],
        created_at=datetime.fromisoformat(payload["creadaEn"].replace("Z", "+00:00")),
    )


def _safe_record_from_json(payload: dict[str, Any]) -> QuoteRequestRecord:
    try:
        return _record_from_json(payload)
    except (KeyError, TypeError, ValueError) as error:
        raise QuoteServiceUnavailableError() from error
