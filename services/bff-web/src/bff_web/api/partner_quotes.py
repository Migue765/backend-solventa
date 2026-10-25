from typing import Literal, cast
from uuid import UUID

from flask import Blueprint, Response, current_app, g, jsonify, request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from bff_web.api.mortgage_profiling import _error_response
from bff_web.api.quotes import QuoteRequestPayload
from bff_web.api.schemas import user_to_json
from bff_web.api.security import audit_rejected_response, partner_claims
from bff_web.application.ports import IdentityGateway
from bff_web.application.quote_ports import QuoteGateway
from bff_web.domain.document_types import validate_identification
from bff_web.domain.mortgage_profiling import MortgageProfilingError
from bff_web.domain.quote_errors import QuoteServiceUnavailableError
from bff_web.infrastructure.http_partner_quote_gateway import HttpPartnerQuoteGateway
from bff_web.problems import problem_response

bp = Blueprint("partner_quotes", __name__, url_prefix="/v1/socios")
bp.after_request(audit_rejected_response)


class PartnerClientPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clienteId: UUID
    tipoCliente: Literal["persona_natural"]
    tipoDocumento: Literal["cc", "ce", "passport", "ppt"]
    numeroDocumento: str
    telefono: str = Field(pattern=r"^\+57[0-9]{10}$")

    @model_validator(mode="after")
    def validate_document(self) -> "PartnerClientPayload":
        self.numeroDocumento = validate_identification(self.tipoDocumento, self.numeroDocumento)
        return self


class PartnerQuotePayload(QuoteRequestPayload):
    operationId: UUID
    socioId: str = Field(min_length=1, max_length=50)
    cliente: PartnerClientPayload
    consentimientoId: UUID
    consentimientoVersion: int = Field(ge=1)


@bp.get("/actual")
def current_partner() -> Response:
    claims = partner_claims()
    if isinstance(claims, Response):
        return claims
    if request.headers.get("API-Version", "1") != "1":
        return _version_error()
    try:
        gateway = cast(QuoteGateway, current_app.extensions["quote_gateway"])
        products = [product.product_id for product in gateway.list_products() if product.available]
    except QuoteServiceUnavailableError:
        return problem_response(
            503,
            "servicio_no_disponible",
            "Servicio no disponible",
            "No se pudo consultar el catalogo.",
        )
    response = jsonify(
        apiVersion="1",
        usuario=user_to_json(claims.user),
        socios=[{"socioId": partner, "productos": products} for partner in claims.user.partner_ids],
        alcances=["perfilamientos:crear", "clientes:consultar"],
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["API-Version"] = "1"
    return response


@bp.post("/solicitudes-cotizacion")
def start() -> tuple[Response, int] | Response:
    claims = partner_claims()
    if isinstance(claims, Response):
        return claims
    if request.headers.get("API-Version", "1") != "1":
        return _version_error()
    try:
        payload = PartnerQuotePayload.model_validate(request.get_json(silent=False))
        key = UUID(request.headers["Idempotency-Key"])
        if key != payload.operationId:
            raise ValueError("operationId debe coincidir con Idempotency-Key")
    except (ValidationError, ValueError, TypeError, KeyError):
        return problem_response(
            400, "solicitud_invalida", "Solicitud invalida", "Revisa los datos."
        )
    if payload.socioId not in claims.user.partner_ids:
        return _partner_error()
    g.audit_partner_id = payload.socioId
    identity = cast(IdentityGateway, current_app.extensions["identity_gateway"])
    client = identity.get_client(payload.cliente.clienteId, str(g.correlation_id))
    if client is None:
        return problem_response(
            404,
            "cliente_no_encontrado",
            "Cliente no encontrado",
            "El cliente debe estar registrado.",
        )
    body: dict[str, object] = {
        "operationId": str(key),
        "socioId": payload.socioId,
        "usuarioId": str(claims.user.user_id),
        "productoId": payload.product_id,
        "datosProducto": payload.product_data,
        "clienteId": str(client.user_id),
        "tipoCliente": payload.cliente.tipoCliente,
        "tipoDocumento": payload.cliente.tipoDocumento,
        "identificacion": payload.cliente.numeroDocumento,
        "telefono": payload.cliente.telefono,
        "correo": client.email,
        "consentimientoId": str(payload.consentimientoId),
        "consentimientoVersion": payload.consentimientoVersion,
    }
    try:
        result = _gateway().start(body, key, str(g.correlation_id))
    except MortgageProfilingError as error:
        return _error_response(error)
    response = jsonify(result.model_dump(mode="json"))
    response.headers["Cache-Control"] = "no-store"
    response.headers["API-Version"] = "1"
    return response, 201


@bp.get("/solicitudes-cotizacion/<uuid:operation_id>")
def get(operation_id: UUID) -> Response:
    claims = partner_claims()
    if isinstance(claims, Response):
        return claims
    if request.headers.get("API-Version", "1") != "1":
        return _version_error()
    partner_id = request.args.get("socioId", "")
    if partner_id not in claims.user.partner_ids:
        return _partner_error()
    g.audit_partner_id = partner_id
    try:
        result = _gateway().get(partner_id, operation_id, str(g.correlation_id))
    except MortgageProfilingError as error:
        return _error_response(error)
    response = jsonify(result.model_dump(mode="json"))
    response.headers["Cache-Control"] = "no-store"
    response.headers["API-Version"] = "1"
    return response


def _gateway() -> HttpPartnerQuoteGateway:
    return cast(HttpPartnerQuoteGateway, current_app.extensions["partner_quote_gateway"])


def _partner_error() -> Response:
    return problem_response(
        403, "socio_no_autorizado", "Socio no autorizado", "La cuenta no pertenece al socio."
    )


def _version_error() -> Response:
    return problem_response(
        400, "version_no_soportada", "Version no soportada", "Usa la version 1 de la API."
    )
