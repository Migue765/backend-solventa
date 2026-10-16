from typing import cast
from uuid import UUID

from flask import Blueprint, Response, current_app, jsonify, request
from pydantic import TypeAdapter, ValidationError

from cotizacion.api.schemas import (
    RegistrarSolicitudRequest,
    SeleccionarOfertaRequest,
    producto_a_json,
    resultado_ofertas_a_json,
    seleccion_oferta_a_json,
    solicitud_a_json,
)
from cotizacion.application.ofertas import GestionarOfertas
from cotizacion.application.ports import ProveedorPerfil
from cotizacion.application.registrar_solicitud import RegistrarSolicitud
from cotizacion.domain.destinos_viaje import DESTINOS_VIAJE
from cotizacion.domain.errors import ErrorConsultaPerfil, SolicitudNoEncontradaError
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


@bp.get("/solicitudes-cotizacion/<solicitud_id>/perfilamiento")
def consultar_perfilamiento(solicitud_id: str) -> tuple[Response, int] | Response:
    if not current_app.extensions["perfilamiento_habilitado"]:
        raise ErrorConsultaPerfil("perfilamiento_no_disponible", 503)
    cliente_id = _cliente_id()
    if cliente_id is None:
        return jsonify(error="solicitud_invalida"), 400
    solicitud = _repositorio().obtener_solicitud(solicitud_id, cliente_id)
    if solicitud is None:
        raise SolicitudNoEncontradaError()
    instantanea = solicitud.cotizacion.instantanea
    if instantanea is None:
        raise ErrorConsultaPerfil("perfil_no_encontrado", 404)
    proveedor = cast(ProveedorPerfil, current_app.extensions["proveedor_perfil"])
    perfil = proveedor.obtener(instantanea.perfil_id, cliente_id)
    if perfil is None or perfil.evidencia is None:
        raise ErrorConsultaPerfil("perfil_no_encontrado", 404)
    if perfil.evidencia["referenciaId"] != str(
        solicitud.cotizacion.cotizacion_id
    ) or perfil.evidencia != instantanea.contenido["perfil"].get("evidencia"):
        raise ErrorConsultaPerfil("perfilamiento_no_disponible", 503)
    datos = {
        key: value
        for key, value in perfil.evidencia.items()
        if key not in {"clienteId", "referenciaId"}
    }
    return jsonify(solicitudId=solicitud_id, **datos)


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


def _registrar() -> RegistrarSolicitud:
    return cast(RegistrarSolicitud, current_app.extensions["registrar_solicitud"])


def _repositorio() -> RepositorioCotizacionesSqlAlchemy:
    return cast(
        RepositorioCotizacionesSqlAlchemy,
        current_app.extensions["repositorio_cotizaciones"],
    )


def _ofertas() -> GestionarOfertas:
    return cast(GestionarOfertas, current_app.extensions["gestionar_ofertas"])


def _cliente_id() -> UUID | None:
    try:
        return UUID_ADAPTER.validate_python(request.args.get("clienteId"))
    except ValidationError:
        return None
