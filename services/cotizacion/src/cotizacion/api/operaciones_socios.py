from dataclasses import replace
from typing import cast
from uuid import UUID

from flask import Blueprint, Response, current_app, jsonify, request
from pydantic import Field, ValidationError

from cotizacion.api.operaciones_hipotecarias import _authorized
from cotizacion.api.schemas import RegistrarSolicitudRequest
from cotizacion.application.iniciar_solicitud_socio import (
    IniciarSolicitudSocio,
    OperacionSocio,
    SolicitudSocioComando,
)
from cotizacion.infrastructure.persistence.admision_socios import AdmisionSocios
from cotizacion.infrastructure.persistence.repositorio_operaciones_socios import (
    RepositorioOperacionesSociosSql,
)

bp = Blueprint("operaciones_socios", __name__, url_prefix="/v1/socios")


class SolicitudSocioRequest(RegistrarSolicitudRequest):
    operation_id: UUID
    socio_id: str = Field(min_length=1, max_length=50)
    usuario_id: UUID
    consentimiento_id: UUID
    consentimiento_version: int = Field(ge=1)


@bp.post("/solicitudes-cotizacion")
def iniciar() -> tuple[Response, int] | Response:
    if not _authorized():
        return jsonify(error="no_autorizado"), 401
    if request.headers.get("API-Version", "1") != "1":
        return jsonify(error="version_no_soportada"), 400
    try:
        payload = SolicitudSocioRequest.model_validate(request.get_json(silent=False))
        key = UUID(request.headers["Idempotency-Key"])
    except (ValidationError, ValueError, KeyError, TypeError):
        return jsonify(error="solicitud_invalida"), 400
    if key != payload.operation_id:
        return jsonify(error="solicitud_invalida"), 400
    caso = cast(IniciarSolicitudSocio, current_app.extensions["iniciar_solicitud_socio"])
    operacion = caso.ejecutar(
        SolicitudSocioComando(
            operation_id=payload.operation_id,
            socio_id=payload.socio_id,
            usuario_id=payload.usuario_id,
            solicitud=replace(
                payload.a_comando(key),
                consentimiento_id=payload.consentimiento_id,
                consentimiento_version=payload.consentimiento_version,
            ),
        )
    )
    response = jsonify(_a_json(operacion))
    response.headers["Cache-Control"] = "no-store"
    return response, 201


@bp.get("/solicitudes-cotizacion/<uuid:operation_id>")
def consultar(operation_id: UUID) -> tuple[Response, int] | Response:
    if not _authorized():
        return jsonify(error="no_autorizado"), 401
    socio = request.args.get("socioId", "")
    if not socio or len(socio) > 50:
        return jsonify(error="solicitud_invalida"), 400
    repositorio = cast(
        RepositorioOperacionesSociosSql, current_app.extensions["operaciones_socios"]
    )
    operacion = repositorio.buscar(socio, operation_id)
    if operacion is not None:
        response = jsonify(_a_json(operacion))
        response.headers["Cache-Control"] = "no-store"
        return response
    admision = cast(AdmisionSocios, current_app.extensions["admision_socios"])
    estado = admision.estado(socio, operation_id)
    if estado is None:
        return jsonify(error="operacion_no_encontrada"), 404
    response = jsonify(error=estado)
    response.headers["Retry-After"] = "1"
    response.headers["Cache-Control"] = "no-store"
    return response, 409


def _a_json(operacion: OperacionSocio) -> dict[str, object]:
    return dict(
        operationId=str(operacion.operation_id),
        socioId=operacion.socio_id,
        clienteId=str(operacion.cliente_id),
        productoId=operacion.producto_id,
        estado="completada",
        perfilamientoReferencia=operacion.perfilamiento_referencia,
        solicitudId=operacion.solicitud_id,
        cotizacionId=str(operacion.cotizacion_id),
        recibidaEn=operacion.creada_en.isoformat().replace("+00:00", "Z"),
    )
