from typing import Literal, cast
from uuid import UUID

from flask import Blueprint, Response, current_app, g, jsonify, request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from bff_web.api.security import audit_rejected_response, partner_claims
from bff_web.application.client_information_ports import ClientInformationGateway
from bff_web.application.ports import IdentityGateway
from bff_web.application.quote_ports import QuoteGateway
from bff_web.domain.client_information import ClientInformationError
from bff_web.domain.quote_errors import QuoteServiceUnavailableError
from bff_web.problems import problem_response

bp = Blueprint("client_information", __name__, url_prefix="/v1/socios")
bp.after_request(audit_rejected_response)


class ClientInformationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operationId: UUID
    socioId: str = Field(min_length=1, max_length=50)
    clienteId: UUID
    productoId: str = Field(min_length=1, max_length=64)
    finalidad: Literal["cotizacion"]
    datosSolicitados: tuple[str, ...] = Field(min_length=1, max_length=20)


@bp.post("/consultas-informacion-cliente")
def consult() -> tuple[Response, int] | Response:
    claims = partner_claims()
    if isinstance(claims, Response):
        return claims
    if request.headers.get("API-Version", "1") != "1":
        return problem_response(
            400, "version_no_soportada", "Version no soportada", "Usa la version 1 de la API."
        )
    try:
        payload = ClientInformationPayload.model_validate(request.get_json(silent=False))
        key = UUID(request.headers["Idempotency-Key"])
    except (ValidationError, KeyError, TypeError, ValueError):
        return _invalid()
    if key != payload.operationId:
        return _invalid()
    if payload.socioId not in claims.user.partner_ids:
        return problem_response(
            403,
            "socio_no_autorizado",
            "Socio no autorizado",
            "La cuenta no pertenece al socio.",
        )
    g.audit_partner_id = payload.socioId
    try:
        products = cast(QuoteGateway, current_app.extensions["quote_gateway"]).list_products()
    except QuoteServiceUnavailableError:
        return _unavailable()
    if not any(
        product.product_id == payload.productoId and product.available for product in products
    ):
        return problem_response(
            403,
            "producto_no_autorizado",
            "Producto no autorizado",
            "El producto no esta disponible para el socio.",
        )
    identity = cast(IdentityGateway, current_app.extensions["identity_gateway"])
    client = identity.get_client(payload.clienteId, str(g.correlation_id))
    if client is None:
        return problem_response(
            404,
            "cliente_no_encontrado",
            "Cliente no encontrado",
            "El cliente debe estar registrado.",
        )
    gateway = cast(
        ClientInformationGateway,
        current_app.extensions["client_information_gateway"],
    )
    try:
        result = gateway.consult(
            {
                "operationId": str(payload.operationId),
                "socioId": payload.socioId,
                "actorId": str(claims.user.user_id),
                "clienteId": str(client.user_id),
                "productoId": payload.productoId,
                "finalidad": payload.finalidad,
                "datosSolicitados": list(payload.datosSolicitados),
            },
            str(g.correlation_id),
        )
    except ClientInformationError as error:
        return _controlled_error(error)
    response = jsonify(result.model_dump(mode="json", by_alias=True))
    response.headers["Cache-Control"] = "no-store"
    response.headers["API-Version"] = "1"
    return response, 201


def _invalid() -> Response:
    return problem_response(
        400,
        "solicitud_invalida",
        "Solicitud invalida",
        "Revisa operationId, socio, cliente, producto, finalidad y datos.",
    )


def _unavailable() -> Response:
    return problem_response(
        503,
        "servicio_no_disponible",
        "Servicio no disponible",
        "No fue posible completar la consulta.",
    )


def _controlled_error(error: ClientInformationError) -> Response:
    codes = {
        "consentimiento_ausente": "consentimiento_invalido",
        "consentimiento_no_vigente": "consentimiento_invalido",
        "producto_no_autorizado": "producto_no_autorizado",
        "finalidad_no_autorizada": "finalidad_no_autorizada",
        "conflicto_idempotencia": "idempotencia_conflicto",
    }
    code = codes.get(error.code, "servicio_no_disponible")
    status = error.status if error.code in codes else 503
    return problem_response(
        status,
        code,
        "Consulta no autorizada" if status == 403 else "Consulta no disponible",
        "No fue posible consultar informacion autorizada del cliente.",
    )
