from datetime import datetime
from typing import cast
from uuid import UUID

from flask import Blueprint, Response, current_app, jsonify, request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pydantic.alias_generators import to_camel

from bff_web.api.authentication import authenticated_user as _authenticated_user
from bff_web.application.quote_ports import QuoteGateway
from bff_web.domain.auth import User
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
    Money,
    Offer,
    OfferSelection,
    OffersResult,
    QuoteRequestDraft,
    QuoteRequestRecord,
    TravelDestination,
)
from bff_web.problems import problem_response

bp = Blueprint("quotes", __name__, url_prefix="/v1")


class QuoteRequestPayload(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    product_id: str = Field(alias="productoId", max_length=64)
    product_data: dict[str, str] = Field(alias="datosProducto")

    @model_validator(mode="after")
    def validate_product_fields(self) -> "QuoteRequestPayload":
        self.product_data = validate_product_data(self.product_id, self.product_data)
        return self

    def to_draft(self, user: User) -> QuoteRequestDraft:
        return QuoteRequestDraft(
            product_id=self.product_id,
            customer_type="persona_natural",
            document_type=user.document_type,
            identification=user.identification,
            product_data=self.product_data,
            email=user.email,
            phone=user.phone,
        )


class OfferSelectionPayload(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    offer_id: UUID = Field(alias="ofertaId")
    version: int = Field(ge=1)


@bp.get("/productos-seguro")
def list_products() -> Response:
    user_id = _authenticated_user_id()
    if user_id is None:
        return _unauthorized()
    try:
        products = _gateway().list_products()
    except QuoteServiceUnavailableError:
        return _unavailable()
    return jsonify(productos=[_product_json(product) for product in products])


@bp.get("/destinos-viaje")
def list_travel_destinations() -> Response:
    if _authenticated_user_id() is None:
        return _unauthorized()
    try:
        destinations = _gateway().list_travel_destinations()
    except QuoteServiceUnavailableError:
        return _unavailable()
    return jsonify(destinos=[_travel_destination_json(destination) for destination in destinations])


@bp.post("/solicitudes-cotizacion")
def create_request() -> tuple[Response, int] | Response:
    user = _authenticated_user()
    if user is None:
        return _unauthorized()
    try:
        payload = QuoteRequestPayload.model_validate(request.get_json(silent=False))
        idempotency_key = UUID(request.headers["Idempotency-Key"])
    except (KeyError, TypeError, ValueError, ValidationError):
        return problem_response(
            400,
            "solicitud_invalida",
            "Solicitud inválida",
            "Revisa los datos y la clave de idempotencia.",
        )
    if payload.product_id == "travel":
        try:
            supported_codes = {
                destination.code for destination in _gateway().list_travel_destinations()
            }
        except QuoteServiceUnavailableError:
            return _unavailable()
        if payload.product_data["destinationCountryCode"] not in supported_codes:
            return problem_response(
                400,
                "solicitud_invalida",
                "Solicitud inválida",
                "Selecciona un país de destino disponible.",
            )
    try:
        record = _gateway().create_request(user.user_id, payload.to_draft(user), idempotency_key)
    except InsuranceProductUnavailableError:
        return problem_response(
            422,
            "seguro_no_disponible",
            "Seguro no disponible",
            "Selecciona otro seguro.",
        )
    except QuoteRequestConflictError:
        return problem_response(
            409,
            "conflicto_idempotencia",
            "Conflicto de solicitud",
            "La clave de idempotencia ya fue utilizada con otros datos.",
        )
    except QuoteServiceUnavailableError:
        return _unavailable()
    return jsonify(_record_json(record)), 201


@bp.get("/solicitudes-cotizacion/<request_id>")
def get_request(request_id: str) -> Response:
    user_id = _authenticated_user_id()
    if user_id is None:
        return _unauthorized()
    try:
        record = _gateway().get_request(user_id, request_id)
    except QuoteRequestNotFoundError:
        return problem_response(
            404,
            "solicitud_no_encontrada",
            "Solicitud no encontrada",
            "La solicitud no existe o no pertenece al cliente.",
        )
    except QuoteServiceUnavailableError:
        return _unavailable()
    return jsonify(_record_json(record))


@bp.get("/solicitudes-cotizacion/<request_id>/ofertas")
def list_offers(request_id: str) -> Response:
    user_id = _authenticated_user_id()
    if user_id is None:
        return _unauthorized()
    try:
        result = _gateway().list_offers(user_id, request_id)
    except QuoteRequestNotFoundError:
        return _not_found()
    except QuoteServiceUnavailableError:
        return _unavailable()
    return jsonify(_offers_result_json(result))


@bp.get("/solicitudes-cotizacion/<request_id>/seleccion")
def get_selection(request_id: str) -> Response:
    user_id = _authenticated_user_id()
    if user_id is None:
        return _unauthorized()
    try:
        selection = _gateway().get_selection(user_id, request_id)
    except QuoteRequestNotFoundError:
        return _not_found()
    except QuoteServiceUnavailableError:
        return _unavailable()
    return jsonify(_selection_json(selection))


@bp.put("/solicitudes-cotizacion/<request_id>/seleccion")
def select_offer(request_id: str) -> Response:
    user_id = _authenticated_user_id()
    if user_id is None:
        return _unauthorized()
    try:
        payload = OfferSelectionPayload.model_validate(request.get_json(silent=False))
        idempotency_key = UUID(request.headers["Idempotency-Key"])
    except (KeyError, TypeError, ValueError, ValidationError):
        return problem_response(
            400,
            "solicitud_invalida",
            "Solicitud inválida",
            "Revisa la oferta, su versión y la clave de idempotencia.",
        )
    try:
        selection = _gateway().select_offer(
            user_id,
            request_id,
            payload.offer_id,
            payload.version,
            idempotency_key,
        )
    except QuoteRequestNotFoundError:
        return _not_found()
    except QuoteRequestConflictError:
        return problem_response(
            409,
            "conflicto_idempotencia",
            "Conflicto de solicitud",
            "La clave de idempotencia ya fue utilizada con otros datos.",
        )
    except OfferUnavailableError:
        return problem_response(
            422,
            "oferta_no_disponible",
            "Oferta no disponible",
            "Actualiza las ofertas y selecciona una vigente.",
        )
    except QuoteServiceUnavailableError:
        return _unavailable()
    return jsonify(_selection_json(selection))


def _authenticated_user_id() -> UUID | None:
    user = _authenticated_user()
    return user.user_id if user else None


def _gateway() -> QuoteGateway:
    return cast(QuoteGateway, current_app.extensions["quote_gateway"])


def _product_json(product: InsuranceProduct) -> dict[str, object]:
    return {
        "id": product.product_id,
        "nombre": product.name,
        "descripcion": product.description,
        "disponible": product.available,
    }


def _travel_destination_json(destination: TravelDestination) -> dict[str, str]:
    return {"codigo": destination.code, "nombre": destination.name}


def _record_json(record: QuoteRequestRecord) -> dict[str, object]:
    return {
        "id": record.request_id,
        "productoId": record.draft.product_id,
        "tipoCliente": record.draft.customer_type,
        "tipoDocumento": record.draft.document_type,
        "identificacion": record.draft.identification,
        "datosProducto": record.draft.product_data,
        "correo": record.draft.email,
        "telefono": record.draft.phone,
        "estado": record.status,
        "creadaEn": record.created_at.isoformat().replace("+00:00", "Z"),
    }


def _offers_result_json(result: OffersResult) -> dict[str, object]:
    return {
        "solicitudId": result.request_id,
        "calculadaEn": _date_json(result.calculated_at),
        "vigenteHasta": _date_json(result.valid_until),
        "tasaAplicada": result.applied_rate,
        "ofertas": [_offer_json(offer) for offer in result.offers],
    }


def _offer_json(offer: Offer) -> dict[str, object]:
    return {
        "id": str(offer.offer_id),
        "version": offer.version,
        "nombre": offer.name,
        "descripcion": offer.description,
        "primaMensual": _money_json(offer.monthly_premium),
        "coberturaPrincipal": offer.main_coverage,
        "deducible": offer.deductible,
        "vigencia": offer.duration,
        "responsabilidadCivil": offer.civil_liability,
        "coberturas": list(offer.coverages),
        "condiciones": list(offer.conditions),
        "beneficiosAdicionales": list(offer.additional_benefits),
        "recomendada": offer.recommended,
        "estado": offer.status,
        "vigenteHasta": _date_json(offer.valid_until),
    }


def _selection_json(selection: OfferSelection) -> dict[str, object]:
    return {
        "solicitudId": selection.request_id,
        "ofertaId": str(selection.offer_id),
        "version": selection.version,
        "nombreOferta": selection.offer_name,
        "primaMensual": _money_json(selection.monthly_premium),
        "seleccionadaEn": _date_json(selection.selected_at),
    }


def _money_json(money: Money) -> dict[str, str]:
    return {"monto": str(money.amount), "moneda": money.currency}


def _date_json(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _unauthorized() -> Response:
    return problem_response(
        401,
        "sesion_invalida",
        "Sesión no válida",
        "Inicia sesión nuevamente.",
    )


def _unavailable() -> Response:
    return problem_response(
        503,
        "servicio_no_disponible",
        "Servicio no disponible",
        "Inténtalo nuevamente.",
    )


def _not_found() -> Response:
    return problem_response(
        404,
        "solicitud_no_encontrada",
        "Solicitud no encontrada",
        "La solicitud o la selección no existe para el cliente.",
    )
