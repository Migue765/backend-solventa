from datetime import UTC, datetime
from typing import Literal, cast
from uuid import UUID

from flask import Blueprint, Response, current_app, g, jsonify, request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from bff_web.api.mortgage_profiling import _error_response
from bff_web.api.schemas import user_to_json
from bff_web.api.security import audit_rejected_response, partner_claims
from bff_web.application.ports import IdentityGateway
from bff_web.application.profiling_ports import ProfilingGateway
from bff_web.application.quote_ports import QuoteGateway
from bff_web.domain.auth import User
from bff_web.domain.document_types import validate_identification
from bff_web.domain.mortgage_profiling import MortgageProfilingError
from bff_web.domain.profiling import ProfilingError
from bff_web.domain.quote_errors import QuoteServiceUnavailableError
from bff_web.infrastructure.http_partner_quote_gateway import HttpPartnerQuoteGateway
from bff_web.problems import problem_response

bp = Blueprint("partner_quotes", __name__, url_prefix="/v1/socios")
bp.after_request(audit_rejected_response)


class PartnerClientPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tipoCliente: Literal["persona_natural"]
    tipoDocumento: Literal["cc", "ce", "passport", "ppt"]
    numeroDocumento: str

    @model_validator(mode="after")
    def validate_document(self) -> "PartnerClientPayload":
        self.numeroDocumento = validate_identification(self.tipoDocumento, self.numeroDocumento)
        return self


class PartnerMortgageData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mortgage_balance: str = Field(alias="mortgageBalance", pattern=r"^[1-9][0-9]{0,15}$")
    credit_reference: str = Field(alias="referenciaCredito", pattern=r"^[A-Za-z0-9-]{4,30}$")


class PartnerQuotePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operationId: UUID
    socioId: str = Field(min_length=1, max_length=50)
    product_id: Literal["mortgage-life"] = Field(alias="productoId")
    product_data: PartnerMortgageData = Field(alias="datosProducto")
    cliente: PartnerClientPayload


class ConsentDecision(BaseModel):
    model_config = ConfigDict(extra="ignore")

    consentimientoId: UUID
    clienteId: UUID
    version: int = Field(ge=1)
    estado: Literal["autorizado", "rechazado", "revocado", "vencido"]
    finalidad: Literal["cotizacion"]
    alcances: tuple[str, ...]
    efectivaEn: datetime
    vigenteHasta: datetime
    actorId: UUID | None = None
    fuentes: tuple[str, ...] = ()
    datos: tuple[str, ...] = ()
    versionTerminos: str | None = None

    def is_current_for(self, client_id: UUID) -> bool:
        return (
            self.estado == "autorizado"
            and self.clienteId == client_id
            and self.actorId == client_id
            and self.versionTerminos == "1.0"
            and {"open_finance", "open_data"}.issubset(self.alcances)
            and {"open_finance", "open_data"}.issubset(self.fuentes)
            and self.datos == ("indicador_riesgo",)
            and self.efectivaEn <= datetime.now(UTC) < self.vigenteHasta
        )


@bp.get("/actual")
def current_partner() -> Response:
    claims = partner_claims()
    if isinstance(claims, Response):
        return claims
    if request.headers.get("API-Version", "1") != "1":
        return _version_error()
    try:
        gateway = cast(QuoteGateway, current_app.extensions["quote_gateway"])
        products = [
            product.product_id
            for product in gateway.list_products()
            if product.available and product.product_id == "mortgage-life"
        ]
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
    resolved = _resolve_client(
        payload.cliente.tipoDocumento,
        payload.cliente.numeroDocumento,
    )
    if isinstance(resolved, Response):
        return resolved
    client, consent = resolved
    if client.phone is None or client.birth_date is None:
        return problem_response(
            422,
            "datos_cliente_incompletos",
            "Datos del cliente incompletos",
            "El cliente no tiene telefono o fecha de nacimiento registrados.",
        )
    body: dict[str, object] = {
        "operationId": str(key),
        "socioId": payload.socioId,
        "usuarioId": str(claims.user.user_id),
        "productoId": payload.product_id,
        "datosProducto": {
            "birthDate": client.birth_date.isoformat(),
            "mortgageBalance": payload.product_data.mortgage_balance,
        },
        "referenciaCredito": payload.product_data.credit_reference,
        "clienteId": str(client.user_id),
        "tipoCliente": payload.cliente.tipoCliente,
        "tipoDocumento": payload.cliente.tipoDocumento,
        "identificacion": payload.cliente.numeroDocumento,
        "telefono": client.phone,
        "correo": client.email,
        "consentimientoId": str(consent.consentimientoId),
        "consentimientoVersion": consent.version,
    }
    try:
        result = _gateway().start(body, key, str(g.correlation_id))
    except MortgageProfilingError as error:
        return _error_response(error)
    response = jsonify(result.model_dump(mode="json"))
    response.headers["Cache-Control"] = "no-store"
    response.headers["API-Version"] = "1"
    return response, 201


@bp.get("/clientes/por-documento")
def find_client_by_document() -> Response:
    claims = partner_claims()
    if isinstance(claims, Response):
        return claims
    if request.headers.get("API-Version", "1") != "1":
        return _version_error()
    partner_id = request.args.get("socioId", "")
    if partner_id not in claims.user.partner_ids:
        return _partner_error()
    document_type = request.args.get("tipoDocumento", "")
    try:
        identification = validate_identification(
            document_type, request.args.get("numeroDocumento", "").strip().upper()
        )
    except ValueError:
        return problem_response(
            400,
            "solicitud_invalida",
            "Solicitud invalida",
            "Revisa el tipo y numero de documento.",
        )
    g.audit_partner_id = partner_id
    client = _find_client(document_type, identification)
    if isinstance(client, Response):
        return client
    consent_status: str
    try:
        consent = _get_consent(client)
        consent_status = (
            "vigente"
            if consent.is_current_for(client.user_id)
            else "insuficiente"
            if consent.estado == "autorizado"
            else consent.estado
        )
    except ProfilingError as error:
        if error.code == "consentimiento_ausente":
            consent_status = "ausente"
        elif error.code == "consentimiento_no_vigente":
            consent_status = "vencido"
        else:
            return problem_response(
                503,
                "servicio_no_disponible",
                "Servicio no disponible",
                "No fue posible verificar el consentimiento.",
            )
    except ValidationError:
        return problem_response(
            503,
            "servicio_no_disponible",
            "Servicio no disponible",
            "La respuesta de consentimiento no es valida.",
        )
    response = jsonify(
        nombre=client.name,
        tipoDocumento=document_type,
        numeroDocumentoEnmascarado=_mask_document(identification),
        consentimientoEstado=consent_status,
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["API-Version"] = "1"
    return response


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


def _resolve_client(
    document_type: str,
    identification: str,
) -> tuple[User, ConsentDecision] | Response:
    client = _find_client(document_type, identification)
    if isinstance(client, Response):
        return client
    try:
        consent = _get_consent(client)
    except ProfilingError as error:
        if error.code in {"consentimiento_ausente", "consentimiento_no_vigente"}:
            return _consent_error()
        return problem_response(
            503,
            "servicio_no_disponible",
            "Servicio no disponible",
            "No fue posible verificar el consentimiento.",
        )
    except ValidationError:
        return problem_response(
            503,
            "servicio_no_disponible",
            "Servicio no disponible",
            "La respuesta de consentimiento no es valida.",
        )
    if not consent.is_current_for(client.user_id):
        return _consent_error()
    return client, consent


def _find_client(document_type: str, identification: str) -> User | Response:
    identity = cast(IdentityGateway, current_app.extensions["identity_gateway"])
    client = identity.get_client_by_document(document_type, identification, str(g.correlation_id))
    if client is None:
        return problem_response(
            404,
            "cliente_no_encontrado",
            "Cliente no encontrado",
            "No existe un cliente registrado con ese documento.",
        )
    return client


def _get_consent(client: User) -> ConsentDecision:
    profiling = cast(ProfilingGateway, current_app.extensions["profiling_gateway"])
    return ConsentDecision.model_validate(
        profiling.get_consent(client.user_id, str(g.correlation_id))
    )


def _consent_error() -> Response:
    return problem_response(
        403,
        "consentimiento_no_vigente",
        "Consentimiento no vigente",
        "El cliente debe renovar su autorizacion antes de continuar.",
    )


def _mask_document(identification: str) -> str:
    visible = min(4, len(identification))
    return "*" * (len(identification) - visible) + identification[-visible:]


def _partner_error() -> Response:
    return problem_response(
        403, "socio_no_autorizado", "Socio no autorizado", "La cuenta no pertenece al socio."
    )


def _version_error() -> Response:
    return problem_response(
        400, "version_no_soportada", "Version no soportada", "Usa la version 1 de la API."
    )
