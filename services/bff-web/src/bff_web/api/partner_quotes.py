from datetime import UTC, datetime
from typing import Literal, cast
from uuid import UUID

from flask import Blueprint, Response, current_app, g, jsonify, request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from werkzeug.exceptions import BadRequest, UnsupportedMediaType

from bff_web.api.mortgage_profiling import _error_response
from bff_web.api.quotes import ProvisionalSelectionPayload
from bff_web.api.schemas import user_to_json
from bff_web.api.security import audit_rejected_response, partner_claims
from bff_web.application.ports import IdentityGateway
from bff_web.application.profiling_ports import ProfilingGateway
from bff_web.application.quote_ports import QuoteGateway
from bff_web.domain.auth import User
from bff_web.domain.document_types import validate_identification
from bff_web.domain.mortgage_profiling import MortgageProfilingError
from bff_web.domain.partner_offer import PartnerOffer as PartnerOfferSnapshot
from bff_web.domain.partner_operation import PartnerOperation
from bff_web.domain.profiling import ProfilingError
from bff_web.domain.quote_errors import QuoteServiceUnavailableError
from bff_web.domain.quote_history import QuoteHistoryEntry
from bff_web.infrastructure.http_partner_quote_gateway import HttpPartnerQuoteGateway
from bff_web.problems import problem_response

bp = Blueprint("partner_quotes", __name__, url_prefix="/v1/socios")
bp.after_request(audit_rejected_response)
PARTNER_PRODUCTS = ("mortgage-life",)


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
            if product.available and product.product_id in PARTNER_PRODUCTS
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
        consent_status = _consent_status(client.user_id)
    except ProfilingError:
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


@bp.get("/clientes")
def list_clients() -> Response:
    claims = partner_claims()
    if isinstance(claims, Response):
        return claims
    if request.headers.get("API-Version", "1") != "1":
        return _version_error()
    partner_id = request.args.get("socioId", "")
    if partner_id not in claims.user.partner_ids:
        return _partner_error()
    g.audit_partner_id = partner_id
    product_id = request.args.get("productoId", "")
    if (
        product_id != "mortgage-life"
        or set(request.args) != {"socioId", "productoId"}
        or any(len(request.args.getlist(key)) != 1 for key in request.args)
    ):
        return problem_response(
            400, "solicitud_invalida", "Solicitud invalida", "Revisa el socio y producto."
        )
    try:
        persisted = _gateway().list_clients(partner_id, product_id, str(g.correlation_id))
        clients = [
            _listed_client(client_id) for client_id in sorted(set(persisted.clientes), key=str)
        ]
    except (MortgageProfilingError, ProfilingError, ValueError):
        return _client_list_unavailable()
    response = jsonify(clientes=clients)
    response.headers["Cache-Control"] = "no-store"
    response.headers["API-Version"] = "1"
    return response


def _listed_client(client_id: UUID) -> dict[str, object]:
    return _client_metadata(client_id, _consent_status(client_id))


def _client_metadata(client_id: UUID, status: str) -> dict[str, object]:
    result: dict[str, object] = {
        "clienteId": str(client_id),
        "nombre": None,
        "tipoDocumento": None,
        "numeroDocumentoEnmascarado": None,
        "consentimientoEstado": status,
    }
    if status == "vigente":
        identity = cast(IdentityGateway, current_app.extensions["identity_gateway"])
        client = identity.get_client(client_id, str(g.correlation_id))
        if client is None or client.user_id != client_id or client.role != "cliente":
            raise MortgageProfilingError("servicio_no_disponible", 503)
        identification = validate_identification(client.document_type, client.identification)
        result.update(
            nombre=client.name,
            tipoDocumento=client.document_type,
            numeroDocumentoEnmascarado=_mask_document(identification),
        )
    return result


def _consent_status(client_id: UUID) -> str:
    return _current_consent(client_id)[0]


def _current_consent(client_id: UUID) -> tuple[str, ConsentDecision | None]:
    try:
        consent = _consent_for_id(client_id)
        status = (
            "vigente"
            if consent.is_current_for(client_id)
            else "insuficiente"
            if consent.estado == "autorizado"
            else consent.estado
        )
        return status, consent
    except ProfilingError as error:
        if error.code == "consentimiento_ausente":
            return "ausente", None
        if error.code == "consentimiento_no_vigente":
            return "vencido", None
        raise


def _client_list_unavailable() -> Response:
    return problem_response(
        503,
        "servicio_no_disponible",
        "Servicio no disponible",
        "No fue posible consultar los clientes cotizados.",
    )


@bp.get("/solicitudes-cotizacion")
def list_requests() -> Response:
    partner_id = _offer_partner()
    if isinstance(partner_id, Response):
        return partner_id
    product_id = request.args.get("productoId")
    if (
        (product_id is not None and product_id not in PARTNER_PRODUCTS)
        or set(request.args) not in ({"socioId"}, {"socioId", "productoId"})
        or any(len(request.args.getlist(key)) != 1 for key in request.args)
        or request.get_data()
    ):
        return problem_response(
            400, "solicitud_invalida", "Solicitud invalida", "Revisa el socio y producto."
        )
    try:
        products = (product_id,) if product_id is not None else PARTNER_PRODUCTS
        persisted = [
            row
            for product in products
            for row in _gateway()
            .list_requests(partner_id, product, str(g.correlation_id))
            .solicitudes
        ]
        persisted.sort(key=lambda row: row.cotizacionId)
        persisted.sort(key=lambda row: row.creadaEn, reverse=True)
        clients: dict[UUID, tuple[dict[str, object], ConsentDecision | None]] = {}
        rows = []
        for row in persisted:
            if (
                row.socioId != partner_id
                or row.productoId not in products
                or row.operationId is None
            ):
                raise MortgageProfilingError("servicio_no_disponible", 503)
            if row.clienteId not in clients:
                status, consent = _current_consent(row.clienteId)
                clients[row.clienteId] = (_client_metadata(row.clienteId, status), consent)
            metadata, consent = clients[row.clienteId]
            rows.append(_history_row(row, metadata, consent))
    except (MortgageProfilingError, ProfilingError, ValueError):
        return problem_response(
            503,
            "servicio_no_disponible",
            "Servicio no disponible",
            "No fue posible consultar las solicitudes cotizadas.",
        )
    response = jsonify(solicitudes=rows)
    response.headers["Cache-Control"] = "no-store"
    response.headers["API-Version"] = "1"
    return response


def _history_row(
    row: QuoteHistoryEntry, metadata: dict[str, object], consent: ConsentDecision | None
) -> dict[str, object]:
    visible = (
        consent is not None
        and consent.is_current_for(row.clienteId)
        and consent.consentimientoId == row.consentimientoId
        and consent.version == row.consentimientoVersion
    )
    return {
        **row.model_dump(mode="json", exclude={"consentimientoId", "consentimientoVersion"}),
        "cliente": metadata,
        "estadoSeleccion": row.estadoSeleccion.model_dump(mode="json") if visible else None,
        "seleccionRestringida": not visible
        and (
            row.estadoSeleccion.seleccion is not None or row.estadoSeleccion.aceptacion is not None
        ),
    }


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
        if result.estadoSeleccion is not None and (
            result.estadoSeleccion.seleccion is not None
            or result.estadoSeleccion.aceptacion is not None
        ):
            offer = _gateway().get_offer(partner_id, operation_id, str(g.correlation_id))
            _validate_offer(offer, partner_id, operation_id)
            if offer.clienteId != result.clienteId:
                raise MortgageProfilingError("servicio_no_disponible", 503)
    except (MortgageProfilingError, ProfilingError) as error:
        return _offer_error_response(error)
    response = jsonify(result.model_dump(mode="json"))
    response.headers["Cache-Control"] = "no-store"
    response.headers["API-Version"] = "1"
    return response


@bp.post("/solicitudes-cotizacion/<uuid:operation_id>/oferta")
def calculate_offer(operation_id: UUID) -> tuple[Response, int] | Response:
    partner_id = _offer_partner()
    if isinstance(partner_id, Response):
        return partner_id
    try:
        offer = _gateway().calculate_offer(partner_id, operation_id, str(g.correlation_id))
        _validate_offer(offer, partner_id, operation_id)
    except (MortgageProfilingError, ProfilingError) as error:
        return _offer_error_response(error)
    return _offer_response(offer), 201


@bp.get("/solicitudes-cotizacion/<uuid:operation_id>/oferta")
def get_offer(operation_id: UUID) -> Response:
    partner_id = _offer_partner()
    if isinstance(partner_id, Response):
        return partner_id
    try:
        offer = _gateway().get_offer(partner_id, operation_id, str(g.correlation_id))
        _validate_offer(offer, partner_id, operation_id)
    except (MortgageProfilingError, ProfilingError) as error:
        return _offer_error_response(error)
    return _offer_response(offer)


@bp.get("/solicitudes-cotizacion/<uuid:operation_id>/seleccion-provisional")
def get_selection(operation_id: UUID) -> Response:
    partner_id = _selection_partner()
    if isinstance(partner_id, Response):
        return partner_id
    try:
        operation, _ = _selection_operation(partner_id, operation_id)
        state = _gateway().get_selection(partner_id, operation_id, str(g.correlation_id))
        if state.solicitudId != operation.solicitudId or any(
            item is not None and item.solicitudId != operation.solicitudId
            for item in (state.seleccion, state.aceptacion)
        ):
            raise MortgageProfilingError("servicio_no_disponible", 503)
    except (MortgageProfilingError, ProfilingError) as error:
        return _offer_error_response(error)
    return _offer_response(state)


@bp.put("/solicitudes-cotizacion/<uuid:operation_id>/seleccion-provisional")
def select_offer(operation_id: UUID) -> Response:
    partner_id = _selection_partner()
    if isinstance(partner_id, Response):
        return partner_id
    try:
        payload = ProvisionalSelectionPayload.model_validate(request.get_json(silent=False))
        key = UUID(request.headers["Idempotency-Key"])
    except (ValidationError, ValueError, TypeError, KeyError, BadRequest, UnsupportedMediaType):
        return problem_response(
            400, "solicitud_invalida", "Solicitud invalida", "Revisa la seleccion y su clave."
        )
    try:
        operation, offer = _selection_operation(partner_id, operation_id)
        chosen = next(
            (
                item
                for item in offer.ofertas
                if item.ofertaId == payload.offer_id and item.version == payload.version
            ),
            None,
        )
        if chosen is None:
            raise MortgageProfilingError("oferta_no_disponible", 422)
        selection = _gateway().select_offer(
            partner_id,
            operation_id,
            payload.model_dump(mode="json", by_alias=True),
            key,
            str(g.correlation_id),
        )
        if (
            selection.solicitudId != operation.solicitudId
            or selection.ofertaId != payload.offer_id
            or selection.version != payload.version
            or selection.revision != payload.expected_revision + 1
            or selection.prima.monto != chosen.prima.monto
            or selection.prima.moneda != chosen.prima.moneda
            or selection.periodicidad != chosen.periodicidad
        ):
            raise MortgageProfilingError("servicio_no_disponible", 503)
    except (MortgageProfilingError, ProfilingError) as error:
        return _offer_error_response(error)
    return _offer_response(selection)


def _selection_partner() -> str | Response:
    partner = _offer_partner()
    if isinstance(partner, Response):
        return partner
    if set(request.args) != {"socioId"} or len(request.args.getlist("socioId")) != 1:
        return problem_response(
            400, "solicitud_invalida", "Solicitud invalida", "Indica solo el socio autorizado."
        )
    return partner


def _selection_operation(
    partner_id: str, operation_id: UUID
) -> tuple[PartnerOperation, PartnerOfferSnapshot]:
    operation = _gateway().get(partner_id, operation_id, str(g.correlation_id))
    offer = _gateway().get_offer(partner_id, operation_id, str(g.correlation_id))
    _validate_offer(offer, partner_id, operation_id)
    if (
        operation.operationId != operation_id
        or operation.socioId != partner_id
        or operation.clienteId != offer.clienteId
        or operation.productoId != offer.productoId
        or operation.solicitudId != offer.solicitudId
        or operation.cotizacionId != offer.cotizacionId
        or operation.oferta.trazabilidad.hashDecision != offer.decision.hashDecision
    ):
        raise MortgageProfilingError("servicio_no_disponible", 503)
    return operation, offer


def _validate_offer(offer: PartnerOfferSnapshot, partner_id: str, operation_id: UUID) -> None:
    if (
        offer.socioId != partner_id
        or offer.operationId != operation_id
        or offer.productoId != "mortgage-life"
    ):
        raise MortgageProfilingError("servicio_no_disponible", 503)
    try:
        consent = _consent_for_id(offer.clienteId)
    except ValidationError as error:
        raise MortgageProfilingError("servicio_no_disponible", 503) from error
    if (
        not consent.is_current_for(offer.clienteId)
        or consent.consentimientoId != offer.riesgo.consentimientoId
        or consent.version != offer.riesgo.consentimientoVersion
    ):
        raise MortgageProfilingError("consentimiento_no_vigente", 403)


def _offer_partner() -> str | Response:
    claims = partner_claims()
    if isinstance(claims, Response):
        return claims
    if request.headers.get("API-Version", "1") != "1":
        return _version_error()
    partner_id = request.args.get("socioId", "")
    if partner_id not in claims.user.partner_ids:
        return _partner_error()
    g.audit_partner_id = partner_id
    return partner_id


def _offer_response(offer: BaseModel) -> Response:
    response = jsonify(offer.model_dump(mode="json"))
    response.headers["Cache-Control"] = "no-store"
    response.headers["API-Version"] = "1"
    return response


def _offer_error_response(error: MortgageProfilingError | ProfilingError) -> Response:
    public = {
        "operacion_no_encontrada": ("operacion_no_encontrada", "Operacion no encontrada"),
        "oferta_no_encontrada": ("oferta_no_encontrada", "Oferta no encontrada"),
        "oferta_no_disponible": ("oferta_no_disponible", "Oferta no disponible"),
        "no_encontrado": ("operacion_no_encontrada", "Operacion no encontrada"),
        "conflicto_idempotencia": ("conflicto_idempotencia", "Conflicto de idempotencia"),
        "seleccion_desactualizada": ("seleccion_desactualizada", "Seleccion desactualizada"),
        "seleccion_no_modificable": ("seleccion_no_modificable", "Seleccion no modificable"),
        "solicitud_invalida": ("solicitud_invalida", "Solicitud invalida"),
        "cotizacion_vencida": ("entradas_vencidas", "Entradas vencidas"),
        "perfil_vencido": ("entradas_vencidas", "Entradas vencidas"),
        "fuente_vencida": ("entradas_vencidas", "Entradas vencidas"),
        "decision_inconsistente": ("oferta_no_disponible", "Oferta no disponible"),
        "decision_sin_tarificacion": ("oferta_no_disponible", "Oferta no disponible"),
        "cotizacion_no_disponible": ("oferta_no_disponible", "Oferta no disponible"),
        "modelo_no_soportado": ("oferta_no_disponible", "Oferta no disponible"),
        "consentimiento_ausente": ("consentimiento_invalido", "Consentimiento invalido"),
        "consentimiento_no_vigente": ("consentimiento_invalido", "Consentimiento invalido"),
    }
    code, title = public.get(error.code, ("servicio_no_disponible", "Servicio no disponible"))
    status = (
        403
        if error.code in {"consentimiento_ausente", "consentimiento_no_vigente"}
        else error.status
        if error.code in public
        else 503
    )
    return problem_response(status, code, title, "No fue posible obtener la oferta.")


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
    return _consent_for_id(client.user_id)


def _consent_for_id(client_id: UUID) -> ConsentDecision:
    profiling = cast(ProfilingGateway, current_app.extensions["profiling_gateway"])
    return ConsentDecision.model_validate(profiling.get_consent(client_id, str(g.correlation_id)))


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
