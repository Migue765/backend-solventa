from typing import cast
from uuid import UUID

from flask import Blueprint, Response, current_app, jsonify, request
from pydantic import TypeAdapter, ValidationError

from cotizacion.api.schemas import (
    RegistrarSolicitudRequest,
    producto_a_json,
    solicitud_a_json,
)
from cotizacion.application.registrar_solicitud import RegistrarSolicitud
from cotizacion.domain.productos import PRODUCTOS
from cotizacion.infrastructure.persistence.repositorio_cotizaciones import (
    RepositorioCotizacionesSqlAlchemy,
)

bp = Blueprint("solicitudes", __name__, url_prefix="/v1")
UUID_ADAPTER: TypeAdapter[UUID] = TypeAdapter(UUID)


@bp.get("/productos-seguro")
def listar_productos() -> Response:
    return jsonify(productos=[producto_a_json(producto) for producto in PRODUCTOS])


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


def _registrar() -> RegistrarSolicitud:
    return cast(RegistrarSolicitud, current_app.extensions["registrar_solicitud"])


def _repositorio() -> RepositorioCotizacionesSqlAlchemy:
    return cast(
        RepositorioCotizacionesSqlAlchemy,
        current_app.extensions["repositorio_cotizaciones"],
    )
