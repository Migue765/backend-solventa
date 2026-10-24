from datetime import date
from decimal import Decimal
from typing import Literal, cast
from uuid import UUID

from flask import Blueprint, Response, current_app, g, jsonify, request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic.alias_generators import to_camel

from bff_web.api.security import authenticate, invalid_session_response
from bff_web.application.mortgage_ports import MortgageProfilingGateway
from bff_web.application.ports import IdentityGateway
from bff_web.domain.auth import AccessClaims, User
from bff_web.domain.errors import InvalidAccessTokenError
from bff_web.domain.mortgage_profiling import (
    MortgageClient,
    MortgageCredit,
    MortgageProfilingCommand,
    MortgageProfilingError,
    MortgageProfilingResult,
)
from bff_web.problems import problem_response

bp = Blueprint("mortgage_profiling", __name__, url_prefix="/v1")


class _Payload(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class ClientPayload(_Payload):
    client_id: UUID = Field(alias="clienteId")
    document_type: str = Field(alias="tipoDocumento", pattern=r"^(cc|ce|passport|ppt)$")
    identification: str = Field(alias="numeroDocumento", pattern=r"^[A-Za-z0-9]{6,15}$")
    phone: str = Field(alias="telefono", pattern=r"^\+57[0-9]{10}$")
    birth_date: date = Field(alias="fechaNacimiento")


class CreditPayload(_Payload):
    reference: str = Field(alias="referencia", pattern=r"^[A-Za-z0-9-]{4,30}$")
    amount: Decimal = Field(alias="monto", ge=1_000_000, decimal_places=0)
    term_months: int = Field(alias="plazoMeses", ge=12, le=360)


class StartPayload(_Payload):
    operation_id: UUID
    product_id: Literal["mortgage-life"] = Field(default="mortgage-life", alias="productoId")
    partner_id: str = Field(alias="socioId", min_length=1, max_length=50)
    client: ClientPayload = Field(alias="cliente")
    credit: CreditPayload = Field(alias="credito")
    consent_id: UUID = Field(alias="consentimientoId")
    consent_version: int = Field(alias="consentimientoVersion", ge=1)


@bp.get("/asesor/clientes")
def list_clients() -> Response:
    claims = _advisor_claims()
    if claims is None:
        return invalid_session_response()
    gateway = cast(IdentityGateway, current_app.extensions["identity_gateway"])
    clients = gateway.list_clients(str(g.correlation_id))
    response = jsonify(
        clientes=[
            {
                "clienteId": str(client.user_id),
                "nombre": client.name,
                "correo": client.email,
            }
            for client in clients
        ]
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.post("/perfilamientos-hipotecarios")
def start() -> tuple[Response, int] | Response:
    claims = _advisor_claims()
    if claims is None:
        return invalid_session_response()
    if request.headers.get("API-Version", "1") != "1":
        return problem_response(
            400, "version_no_soportada", "Version no soportada", "Usa la version 1 de la API."
        )
    try:
        payload = StartPayload.model_validate(request.get_json(silent=False))
        idempotency_key = UUID(request.headers["Idempotency-Key"])
    except (KeyError, TypeError, ValueError, ValidationError):
        return _invalid_request()
    if idempotency_key != payload.operation_id:
        return _invalid_request()
    if payload.partner_id not in claims.user.partner_ids:
        return problem_response(
            403,
            "socio_no_autorizado",
            "Socio no autorizado",
            "La cuenta no esta autorizada para operar en este socio.",
        )
    identity = cast(IdentityGateway, current_app.extensions["identity_gateway"])
    selected = identity.get_client(payload.client.client_id, str(g.correlation_id))
    if selected is None:
        return problem_response(
            404,
            "cliente_no_encontrado",
            "Cliente no encontrado",
            "Selecciona un cliente registrado.",
        )
    command = _command(payload, claims.user, selected)
    gateway = cast(
        MortgageProfilingGateway,
        current_app.extensions["mortgage_profiling_gateway"],
    )
    try:
        result = gateway.start(command, str(g.correlation_id))
    except MortgageProfilingError as error:
        return _error_response(error)
    response = jsonify(_result_json(result))
    response.headers["Cache-Control"] = "no-store"
    response.headers["API-Version"] = "1"
    return response, 201


def _advisor_claims() -> AccessClaims | None:
    try:
        claims = authenticate()
    except InvalidAccessTokenError:
        return None
    if claims.scope != "asesor" or claims.user.role != "asesor":
        return None
    return claims


def _command(payload: StartPayload, advisor: User, client: User) -> MortgageProfilingCommand:
    return MortgageProfilingCommand(
        operation_id=payload.operation_id,
        partner_id=payload.partner_id,
        advisor_id=advisor.user_id,
        client=MortgageClient(
            client_id=client.user_id,
            document_type=payload.client.document_type,
            identification=payload.client.identification,
            email=client.email,
            phone=payload.client.phone,
            birth_date=payload.client.birth_date,
        ),
        credit=MortgageCredit(
            reference=payload.credit.reference,
            amount=payload.credit.amount,
            term_months=payload.credit.term_months,
        ),
        consent_id=payload.consent_id,
        consent_version=payload.consent_version,
    )


def _result_json(result: MortgageProfilingResult) -> dict[str, object]:
    return {
        "operationId": str(result.operation_id),
        "socioId": result.partner_id,
        "clienteId": str(result.client_id),
        "productoId": "mortgage-life",
        "estado": result.status,
        "perfilamientoReferencia": result.profiling_reference,
        "solicitudId": result.request_id,
        "cotizacionId": str(result.quote_id),
        "recibidaEn": result.received_at.isoformat().replace("+00:00", "Z"),
    }


def _invalid_request() -> Response:
    return problem_response(
        400,
        "datos_incompletos",
        "Datos incompletos",
        "La solicitud contiene datos incompletos o invalidos.",
    )


def _error_response(error: MortgageProfilingError) -> Response:
    public = {
        "solicitud_invalida": ("datos_incompletos", "Datos incompletos"),
        "consentimiento_ausente": ("consentimiento_invalido", "Consentimiento invalido"),
        "consentimiento_no_vigente": (
            "consentimiento_invalido",
            "Consentimiento invalido",
        ),
        "conflicto_idempotencia": (
            "idempotencia_conflicto",
            "Conflicto de idempotencia",
        ),
        "perfil_vencido": ("perfilamiento_fallido", "Perfilamiento fallido"),
        "regla_de_negocio": ("perfilamiento_fallido", "Perfilamiento fallido"),
        "operacion_en_curso": ("operacion_en_curso", "Operacion en curso"),
        "cuota_socio_excedida": ("cuota_socio_excedida", "Cuota del socio excedida"),
        "socio_ocupado": ("socio_ocupado", "Socio ocupado"),
        "operacion_no_encontrada": ("operacion_no_encontrada", "Operacion no encontrada"),
        "operacion_reintentable": ("operacion_reintentable", "Operacion sin resultado"),
    }
    code, title = public.get(error.code, ("servicio_no_disponible", "Servicio no disponible"))
    status = error.status if error.code in public else 503
    response = problem_response(status, code, title, "No fue posible completar el perfilamiento.")
    if error.retry_after is not None:
        response.headers["Retry-After"] = str(error.retry_after)
    return response
