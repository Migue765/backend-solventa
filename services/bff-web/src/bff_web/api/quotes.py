from datetime import datetime
from typing import cast
from uuid import UUID

from flask import Blueprint, Response, current_app, jsonify, request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pydantic.alias_generators import to_camel
from werkzeug.exceptions import BadRequest, UnsupportedMediaType

from bff_web.api.security import authenticate, invalid_session_response
from bff_web.application.quote_ports import QuoteGateway
from bff_web.domain.auth import User
from bff_web.domain.errors import InvalidAccessTokenError
from bff_web.domain.product_data import validate_product_data
from bff_web.domain.quote_errors import (
    InsuranceProductNotFoundError,
    InsuranceProductUnavailableError,
    OfferUnavailableError,
    PaymentNotAllowedError,
    QuoteRequestConflictError,
    QuoteRequestNotFoundError,
    QuoteServiceUnavailableError,
    SimulatedPaymentNotFoundError,
)
from bff_web.domain.quotes import (
    InsuranceProduct,
    InsuranceProductDetail,
    Money,
    Offer,
    OfferSelection,
    OffersResult,
    QuoteRequestDraft,
    QuoteRequestRecord,
    SimulatedPayment,
    Subscription,
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


class SimulatedPaymentPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")


@bp.get("/clientes/me/dashboard")
def get_dashboard() -> Response:
    user_id = _authenticated_user_id()
    if user_id is None:
        return _unauthorized()
    if request.args or request.get_data():
        return problem_response(
            400,
            "solicitud_invalida",
            "Solicitud invalida",
            "El cliente se obtiene exclusivamente de la sesion autenticada.",
        )
    try:
        dashboard = _gateway().get_dashboard(user_id)
    except QuoteServiceUnavailableError:
        return _unavailable()
    response = jsonify(dashboard.model_dump(mode="json", by_alias=True))
    response.headers["Cache-Control"] = "no-store"
    return response


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


@bp.get("/productos-seguro/<product_id>")
def get_product(product_id: str) -> Response:
    if _authenticated_user_id() is None:
        return _unauthorized()
    try:
        product = _gateway().get_product(product_id)
    except InsuranceProductNotFoundError:
        return problem_response(
            404,
            "seguro_no_encontrado",
            "Seguro no encontrado",
            "El seguro consultado no existe o ya no está disponible.",
        )
    except QuoteServiceUnavailableError:
        return _unavailable()
    return jsonify(_product_detail_json(product))


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


@bp.post("/solicitudes-cotizacion/<request_id>/suscripcion")
def start_subscription(request_id: str) -> tuple[Response, int] | Response:
    user_id = _authenticated_user_id()
    if user_id is None:
        return _unauthorized()
    try:
        idempotency_key = UUID(request.headers["Idempotency-Key"])
    except (KeyError, ValueError):
        return problem_response(
            400,
            "solicitud_invalida",
            "Solicitud inválida",
            "Incluye una clave de idempotencia válida.",
        )
    try:
        subscription = _gateway().start_subscription(user_id, request_id, idempotency_key)
    except QuoteRequestNotFoundError:
        return _not_found()
    except QuoteRequestConflictError:
        return problem_response(
            409,
            "conflicto_idempotencia",
            "Conflicto de solicitud",
            "La clave de idempotencia ya fue utilizada para otra operación.",
        )
    except QuoteServiceUnavailableError:
        return _unavailable()
    return jsonify(_subscription_json(subscription)), 201


@bp.get("/solicitudes-cotizacion/<request_id>/suscripcion")
def get_subscription(request_id: str) -> Response:
    user_id = _authenticated_user_id()
    if user_id is None:
        return _unauthorized()
    try:
        subscription = _gateway().get_subscription(user_id, request_id)
    except QuoteRequestNotFoundError:
        return _not_found()
    except QuoteServiceUnavailableError:
        return _unavailable()
    return jsonify(_subscription_json(subscription))


@bp.post("/solicitudes-cotizacion/<request_id>/pago-simulado")
def confirm_simulated_payment(request_id: str) -> Response:
    user_id = _authenticated_user_id()
    if user_id is None:
        return _unauthorized()
    try:
        idempotency_key = UUID(request.headers["Idempotency-Key"])
        SimulatedPaymentPayload.model_validate(
            request.get_json(silent=False) if request.get_data() else {}
        )
    except (KeyError, ValueError, TypeError, ValidationError, BadRequest, UnsupportedMediaType):
        return problem_response(
            400,
            "solicitud_invalida",
            "Solicitud inválida",
            "Envía solo un objeto vacío y una clave de idempotencia UUID válida.",
        )
    try:
        payment = _gateway().confirm_simulated_payment(user_id, request_id, idempotency_key)
    except QuoteRequestNotFoundError:
        return _not_found()
    except QuoteRequestConflictError:
        return problem_response(
            409,
            "conflicto_idempotencia",
            "Conflicto de solicitud",
            "La clave de idempotencia ya fue utilizada para otra solicitud.",
        )
    except PaymentNotAllowedError:
        return problem_response(
            422,
            "pago_no_permitido",
            "Pago simulado no permitido",
            "Se requiere una suscripción aprobada, oferta vigente y pago pendiente.",
        )
    except QuoteServiceUnavailableError:
        return _unavailable()
    return jsonify(_payment_json(payment))


@bp.get("/solicitudes-cotizacion/<request_id>/pago-simulado")
def get_simulated_payment(request_id: str) -> Response:
    user_id = _authenticated_user_id()
    if user_id is None:
        return _unauthorized()
    try:
        payment = _gateway().get_simulated_payment(user_id, request_id)
    except SimulatedPaymentNotFoundError:
        return problem_response(
            404,
            "pago_no_encontrado",
            "Pago simulado no encontrado",
            "Esta solicitud todavía no tiene un pago simulado confirmado.",
        )
    except QuoteRequestNotFoundError:
        return _not_found()
    except QuoteServiceUnavailableError:
        return _unavailable()
    return jsonify(_payment_json(payment))


def _payment_json(payment: SimulatedPayment) -> dict[str, object]:
    return {
        "id": str(payment.payment_id),
        "solicitudId": payment.request_id,
        "suscripcionId": str(payment.subscription_id),
        "ofertaId": str(payment.offer_id),
        "ofertaVersion": payment.offer_version,
        "estado": "confirmado",
        "simulada": True,
        "referenciaInterna": f"SIM-{payment.payment_id}",
        "importe": _money_json(payment.amount),
        "periodicidad": payment.billing_period,
        "confirmadaEn": _date_json(payment.confirmed_at),
        "saga": {
            "estado": payment.saga.status,
            "version": payment.saga.version,
            "actualizadaEn": _date_json(payment.saga.updated_at),
        },
    }


def _authenticated_user_id() -> UUID | None:
    user = _authenticated_user()
    return user.user_id if user else None


def _authenticated_user() -> User | None:
    try:
        return authenticate().user
    except InvalidAccessTokenError:
        return None


def _gateway() -> QuoteGateway:
    return cast(QuoteGateway, current_app.extensions["quote_gateway"])


def _product_json(product: InsuranceProduct) -> dict[str, object]:
    return {
        "id": product.product_id,
        "nombre": product.name,
        "descripcion": product.description,
        "disponible": product.available,
        "coberturasPrincipales": list(product.main_coverages),
    }


def _product_detail_json(product: InsuranceProductDetail) -> dict[str, object]:
    return {
        "id": product.product_id,
        "nombre": product.name,
        "descripcion": product.description,
        "disponible": product.available,
        "coberturasPrincipales": [coverage.name for coverage in product.coverages],
        "coberturas": [
            {"nombre": coverage.name, "descripcion": coverage.description}
            for coverage in product.coverages
        ],
        "condiciones": list(product.conditions),
        "informacionRelevante": {
            "sumaAsegurada": str(product.insured_amount),
            "deducible": str(product.deductible),
            "moneda": product.currency,
            "vigencia": product.term,
        },
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
        "prima": _money_json(offer.monthly_premium),
        "periodicidad": offer.billing_period,
        "primaMensual": (
            _money_json(offer.monthly_premium) if offer.billing_period == "mensual" else None
        ),
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
        "prima": _money_json(selection.monthly_premium),
        "periodicidad": selection.billing_period,
        "primaMensual": (
            _money_json(selection.monthly_premium)
            if selection.billing_period == "mensual"
            else None
        ),
        "seleccionadaEn": _date_json(selection.selected_at),
    }


def _subscription_json(subscription: Subscription) -> dict[str, object]:
    return {
        "id": str(subscription.subscription_id),
        "solicitudId": subscription.request_id,
        "ofertaId": str(subscription.offer_id),
        "ofertaVersion": subscription.offer_version,
        "estado": subscription.status,
        "reglaVersion": subscription.rule_version,
        "creadaEn": _date_json(subscription.created_at),
        "actualizadaEn": _date_json(subscription.updated_at),
        "decision": {
            "resultado": subscription.decision.result,
            "reglaVersion": subscription.decision.rule_version,
            "hashDecision": subscription.decision.decision_hash,
            "decididaEn": _date_json(subscription.decision.decided_at),
        },
        "saga": {
            "estado": subscription.saga.status,
            "version": subscription.saga.version,
            "actualizadaEn": _date_json(subscription.saga.updated_at),
        },
    }


def _money_json(money: Money) -> dict[str, str]:
    return {"monto": str(money.amount), "moneda": money.currency}


def _date_json(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _unauthorized() -> Response:
    return invalid_session_response()


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
