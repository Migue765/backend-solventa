import json
from datetime import datetime
from decimal import Decimal
from http import HTTPStatus
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import UUID

from bff_web.application.quote_ports import QuoteGateway
from bff_web.domain.product_data import validate_product_data
from bff_web.domain.quote_errors import (
    InsuranceProductUnavailableError,
    OfferUnavailableError,
    QuoteRequestConflictError,
    QuoteRequestNotFoundError,
    QuoteServiceUnavailableError,
)
from bff_web.domain.quotes import (
    InsuranceProduct,
    IssuanceSaga,
    Money,
    Offer,
    OfferSelection,
    OffersResult,
    QuoteRequestDraft,
    QuoteRequestRecord,
    Subscription,
    SubscriptionDecision,
    TravelDestination,
)


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

    def list_travel_destinations(self) -> tuple[TravelDestination, ...]:
        payload = self._request_json("/v1/destinos-viaje")
        try:
            return tuple(
                TravelDestination(code=item["codigo"], name=item["nombre"])
                for item in payload["destinos"]
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
                "tipoDocumento": draft.document_type,
                "identificacion": draft.identification,
                "datosProducto": draft.product_data,
                "correo": draft.email,
                "telefono": draft.phone,
            },
        )
        return _safe_record_from_json(payload)

    def get_request(self, user_id: UUID, request_id: str) -> QuoteRequestRecord:
        query = urlencode({"clienteId": str(user_id)})
        payload = self._request_json(f"/v1/solicitudes-cotizacion/{request_id}?{query}")
        return _safe_record_from_json(payload)

    def list_offers(self, user_id: UUID, request_id: str) -> OffersResult:
        query = urlencode({"clienteId": str(user_id)})
        payload = self._request_json(f"/v1/solicitudes-cotizacion/{request_id}/ofertas?{query}")
        try:
            return OffersResult(
                request_id=payload["solicitudId"],
                calculated_at=_datetime(payload["calculadaEn"]),
                valid_until=_datetime(payload["vigenteHasta"]),
                applied_rate=payload["tasaAplicada"],
                offers=tuple(_offer_from_json(item) for item in payload["ofertas"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise QuoteServiceUnavailableError() from error

    def get_selection(self, user_id: UUID, request_id: str) -> OfferSelection:
        query = urlencode({"clienteId": str(user_id)})
        payload = self._request_json(f"/v1/solicitudes-cotizacion/{request_id}/seleccion?{query}")
        return _safe_selection_from_json(payload)

    def select_offer(
        self,
        user_id: UUID,
        request_id: str,
        offer_id: UUID,
        version: int,
        idempotency_key: UUID,
    ) -> OfferSelection:
        query = urlencode({"clienteId": str(user_id)})
        payload = self._request_json(
            f"/v1/solicitudes-cotizacion/{request_id}/seleccion?{query}",
            method="PUT",
            headers={"Idempotency-Key": str(idempotency_key)},
            body={"ofertaId": str(offer_id), "version": version},
        )
        return _safe_selection_from_json(payload)

    def start_subscription(
        self, user_id: UUID, request_id: str, idempotency_key: UUID
    ) -> Subscription:
        query = urlencode({"clienteId": str(user_id)})
        payload = self._request_json(
            f"/v1/solicitudes-cotizacion/{request_id}/suscripcion?{query}",
            method="POST",
            headers={"Idempotency-Key": str(idempotency_key)},
        )
        return _safe_subscription_from_json(payload)

    def get_subscription(self, user_id: UUID, request_id: str) -> Subscription:
        query = urlencode({"clienteId": str(user_id)})
        payload = self._request_json(f"/v1/solicitudes-cotizacion/{request_id}/suscripcion?{query}")
        return _safe_subscription_from_json(payload)

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
                try:
                    payload = json.load(error)
                except json.JSONDecodeError:
                    raise QuoteServiceUnavailableError() from error
                if payload.get("error") == "oferta_no_disponible":
                    raise OfferUnavailableError() from error
                raise InsuranceProductUnavailableError() from error
            raise QuoteServiceUnavailableError() from error
        except (URLError, TimeoutError, json.JSONDecodeError) as error:
            raise QuoteServiceUnavailableError() from error

        if not isinstance(payload, dict):
            raise QuoteServiceUnavailableError()
        return payload


def _record_from_json(payload: dict[str, Any]) -> QuoteRequestRecord:
    product_id = payload["productoId"]
    draft = QuoteRequestDraft(
        product_id=product_id,
        customer_type=payload["tipoCliente"],
        document_type=payload["tipoDocumento"],
        identification=payload["identificacion"],
        product_data=validate_product_data(
            product_id, payload["datosProducto"], for_creation=False
        ),
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


def _offer_from_json(payload: dict[str, Any]) -> Offer:
    return Offer(
        offer_id=UUID(payload["id"]),
        version=payload["version"],
        name=payload["nombre"],
        description=payload["descripcion"],
        monthly_premium=_premium_from_json(payload),
        main_coverage=payload["coberturaPrincipal"],
        deductible=payload["deducible"],
        duration=payload["vigencia"],
        civil_liability=payload["responsabilidadCivil"],
        coverages=tuple(payload["coberturas"]),
        conditions=tuple(payload["condiciones"]),
        additional_benefits=tuple(payload["beneficiosAdicionales"]),
        recommended=payload["recomendada"],
        status=payload["estado"],
        valid_until=_datetime(payload["vigenteHasta"]),
        billing_period=payload.get("periodicidad", "mensual"),
    )


def _selection_from_json(payload: dict[str, Any]) -> OfferSelection:
    return OfferSelection(
        request_id=payload["solicitudId"],
        offer_id=UUID(payload["ofertaId"]),
        version=payload["version"],
        offer_name=payload["nombreOferta"],
        monthly_premium=_premium_from_json(payload),
        selected_at=_datetime(payload["seleccionadaEn"]),
        billing_period=payload.get("periodicidad", "mensual"),
    )


def _safe_selection_from_json(payload: dict[str, Any]) -> OfferSelection:
    try:
        return _selection_from_json(payload)
    except (KeyError, TypeError, ValueError) as error:
        raise QuoteServiceUnavailableError() from error


def _safe_subscription_from_json(payload: dict[str, Any]) -> Subscription:
    try:
        decision = payload["decision"]
        saga = payload["saga"]
        return Subscription(
            subscription_id=UUID(payload["id"]),
            request_id=payload["solicitudId"],
            offer_id=UUID(payload["ofertaId"]),
            offer_version=payload["ofertaVersion"],
            status=payload["estado"],
            rule_version=payload["reglaVersion"],
            created_at=_datetime(payload["creadaEn"]),
            updated_at=_datetime(payload["actualizadaEn"]),
            decision=SubscriptionDecision(
                result=decision["resultado"],
                rule_version=decision["reglaVersion"],
                decision_hash=decision["hashDecision"],
                decided_at=_datetime(decision["decididaEn"]),
            ),
            saga=IssuanceSaga(
                status=saga["estado"],
                version=saga["version"],
                updated_at=_datetime(saga["actualizadaEn"]),
            ),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise QuoteServiceUnavailableError() from error


def _money_from_json(payload: dict[str, Any]) -> Money:
    return Money(Decimal(payload["monto"]), payload["moneda"])


def _premium_from_json(payload: dict[str, Any]) -> Money:
    if "prima" not in payload and "periodicidad" not in payload:
        return _money_from_json(payload["primaMensual"])
    period = payload["periodicidad"]
    if period not in ("mensual", "por_viaje", "por_vuelo", "por_periodo"):
        raise ValueError("Periodicidad de prima desconocida.")
    return _money_from_json(payload["prima"])


def _datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
