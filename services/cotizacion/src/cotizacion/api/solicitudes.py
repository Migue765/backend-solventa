from typing import cast
from uuid import UUID

from flask import Blueprint, Response, current_app, jsonify, request
from pydantic import TypeAdapter, ValidationError

from cotizacion.api.schemas import (
    ProcesarPagoRequest,
    RegistrarSolicitudRequest,
    SeleccionarOfertaRequest,
    pago_emision_a_json,
    producto_a_json,
    resultado_ofertas_a_json,
    seleccion_oferta_a_json,
    solicitud_a_json,
    suscripcion_a_json,
)
from cotizacion.application.ofertas import GestionarOfertas
from cotizacion.application.pagos_emision import ProcesarPagoYEmision
from cotizacion.application.registrar_solicitud import RegistrarSolicitud
from cotizacion.application.suscripciones import ProcesarSuscripcion
from cotizacion.domain.destinos_viaje import DESTINOS_VIAJE
from cotizacion.domain.productos import PRODUCTOS
from cotizacion.infrastructure.persistence.repositorio_cotizaciones import (
    RepositorioCotizacionesSqlAlchemy,
)

bp = Blueprint("solicitudes", __name__, url_prefix="/v1")
UUID_ADAPTER: TypeAdapter[UUID] = TypeAdapter(UUID)


@bp.get("/productos-seguro")
def listar_productos() -> Response:
    return jsonify(productos=[producto_a_json(producto) for producto in PRODUCTOS])


@bp.get("/destinos-viaje")
def listar_destinos_viaje() -> Response:
    return jsonify(
        destinos=[
            {"codigo": destino.codigo, "nombre": destino.nombre}
            for destino in sorted(DESTINOS_VIAJE, key=lambda item: item.nombre)
        ]
    )


@bp.post("/solicitudes-cotizacion")
def registrar_solicitud() -> tuple[Response, int] | Response:
    try:
        payload = RegistrarSolicitudRequest.model_validate(request.get_json(silent=False))
        idempotency_key = UUID_ADAPTER.validate_python(request.headers.get("Idempotency-Key"))
    except (ValidationError, TypeError):
        return jsonify(error="solicitud_invalida"), 400

    solicitud = _registrar().ejecutar(payload.a_comando(idempotency_key))
    return jsonify(solicitud_a_json(solicitud)), 201


@bp.get("/solicitudes-cotizacion/<solicitud_id>")
def consultar_solicitud(solicitud_id: str) -> tuple[Response, int] | Response:
    try:
        cliente_id = UUID_ADAPTER.validate_python(request.args.get("clienteId"))
    except ValidationError:
        return jsonify(error="solicitud_invalida"), 400

    solicitud = _repositorio().obtener_solicitud(solicitud_id, cliente_id)
    if solicitud is None:
        return jsonify(error="no_encontrado"), 404
    return jsonify(solicitud_a_json(solicitud))


@bp.get("/solicitudes-cotizacion/<solicitud_id>/ofertas")
def listar_ofertas(solicitud_id: str) -> tuple[Response, int] | Response:
    cliente_id = _cliente_id()
    if cliente_id is None:
        return jsonify(error="solicitud_invalida"), 400
    return jsonify(resultado_ofertas_a_json(_ofertas().listar(solicitud_id, cliente_id)))


@bp.get("/solicitudes-cotizacion/<solicitud_id>/seleccion")
def consultar_seleccion(solicitud_id: str) -> tuple[Response, int] | Response:
    cliente_id = _cliente_id()
    if cliente_id is None:
        return jsonify(error="solicitud_invalida"), 400
    return jsonify(
        seleccion_oferta_a_json(_ofertas().consultar_seleccion(solicitud_id, cliente_id))
    )


@bp.put("/solicitudes-cotizacion/<solicitud_id>/seleccion")
def seleccionar_oferta(solicitud_id: str) -> tuple[Response, int] | Response:
    cliente_id = _cliente_id()
    if cliente_id is None:
        return jsonify(error="solicitud_invalida"), 400
    try:
        payload = SeleccionarOfertaRequest.model_validate(request.get_json(silent=False))
        idempotency_key = UUID_ADAPTER.validate_python(request.headers.get("Idempotency-Key"))
    except (ValidationError, TypeError):
        return jsonify(error="solicitud_invalida"), 400
    seleccion = _ofertas().seleccionar(
        solicitud_id,
        cliente_id,
        payload.oferta_id,
        payload.version,
        idempotency_key,
    )
    return jsonify(seleccion_oferta_a_json(seleccion))


@bp.post("/solicitudes-cotizacion/<solicitud_id>/suscripcion")
def iniciar_suscripcion(solicitud_id: str) -> tuple[Response, int] | Response:
    cliente_id = _cliente_id()
    if cliente_id is None:
        return jsonify(error="solicitud_invalida"), 400
    try:
        idempotency_key = UUID_ADAPTER.validate_python(request.headers.get("Idempotency-Key"))
    except ValidationError:
        return jsonify(error="solicitud_invalida"), 400
    suscripcion = _suscripciones().iniciar(solicitud_id, cliente_id, idempotency_key)
    return jsonify(suscripcion_a_json(suscripcion)), 201


@bp.get("/solicitudes-cotizacion/<solicitud_id>/suscripcion")
def consultar_suscripcion(solicitud_id: str) -> tuple[Response, int] | Response:
    cliente_id = _cliente_id()
    if cliente_id is None:
        return jsonify(error="solicitud_invalida"), 400
    return jsonify(suscripcion_a_json(_suscripciones().consultar(solicitud_id, cliente_id)))


@bp.post("/solicitudes-cotizacion/<solicitud_id>/pago")
def procesar_pago(solicitud_id: str) -> tuple[Response, int] | Response:
    cliente_id = _cliente_id()
    if cliente_id is None:
        return jsonify(error="solicitud_invalida"), 400
    try:
        payload = ProcesarPagoRequest.model_validate(request.get_json(silent=False))
        idempotency_key = UUID_ADAPTER.validate_python(request.headers.get("Idempotency-Key"))
    except (ValidationError, TypeError):
        return jsonify(error="solicitud_invalida"), 400
    resultado = _pagos_emision().procesar(
        solicitud_id, cliente_id, payload.metodo_pago, idempotency_key
    )
    return jsonify(pago_emision_a_json(resultado)), 201


@bp.get("/solicitudes-cotizacion/<solicitud_id>/poliza")
def consultar_poliza(solicitud_id: str) -> tuple[Response, int] | Response:
    cliente_id = _cliente_id()
    if cliente_id is None:
        return jsonify(error="solicitud_invalida"), 400
    return jsonify(pago_emision_a_json(_pagos_emision().consultar(solicitud_id, cliente_id)))


def _registrar() -> RegistrarSolicitud:
    return cast(RegistrarSolicitud, current_app.extensions["registrar_solicitud"])


def _repositorio() -> RepositorioCotizacionesSqlAlchemy:
    return cast(
        RepositorioCotizacionesSqlAlchemy,
        current_app.extensions["repositorio_cotizaciones"],
    )


def _ofertas() -> GestionarOfertas:
    return cast(GestionarOfertas, current_app.extensions["gestionar_ofertas"])


def _suscripciones() -> ProcesarSuscripcion:
    return cast(ProcesarSuscripcion, current_app.extensions["procesar_suscripcion"])


def _pagos_emision() -> ProcesarPagoYEmision:
    return cast(ProcesarPagoYEmision, current_app.extensions["procesar_pago_emision"])


def _cliente_id() -> UUID | None:
    try:
        return UUID_ADAPTER.validate_python(request.args.get("clienteId"))
    except ValidationError:
        return None
