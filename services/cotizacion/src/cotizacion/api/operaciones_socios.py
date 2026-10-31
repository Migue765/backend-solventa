from dataclasses import replace
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from flask import Blueprint, Response, current_app, jsonify, request
from pydantic import Field, ValidationError

from cotizacion.api.operaciones_hipotecarias import _authorized
from cotizacion.api.schemas import RegistrarSolicitudRequest, resultado_ofertas_a_json
from cotizacion.application.iniciar_solicitud_socio import (
    IniciarSolicitudSocio,
    OperacionSocio,
    SolicitudSocioComando,
)
from cotizacion.application.ofertas_socio import CalcularOfertaSocio
from cotizacion.domain.oferta_socio import OfertaSocio
from cotizacion.application.ofertas import GestionarOfertas
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
    referencia_credito: str = Field(pattern=r"^[A-Za-z0-9-]{4,30}$")


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
                credito_id=payload.referencia_credito,
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


@bp.post("/solicitudes-cotizacion/<uuid:operation_id>/oferta")
def calcular_oferta(operation_id: UUID) -> tuple[Response, int] | Response:
    if not _authorized():
        return jsonify(error="no_autorizado"), 401
    socio = _socio_valido()
    if socio is None:
        return jsonify(error="solicitud_invalida"), 400
    caso = cast(CalcularOfertaSocio, current_app.extensions["calcular_oferta_socio"])
    response = jsonify(_oferta_a_json(caso.ejecutar(socio, operation_id)))
    response.headers["Cache-Control"] = "no-store"
    return response, 201


@bp.get("/solicitudes-cotizacion/<uuid:operation_id>/oferta")
def consultar_oferta(operation_id: UUID) -> tuple[Response, int] | Response:
    if not _authorized():
        return jsonify(error="no_autorizado"), 401
    socio = _socio_valido()
    if socio is None:
        return jsonify(error="solicitud_invalida"), 400
    caso = cast(CalcularOfertaSocio, current_app.extensions["calcular_oferta_socio"])
    response = jsonify(_oferta_a_json(caso.consultar(socio, operation_id)))
    response.headers["Cache-Control"] = "no-store"
    return response


def _socio_valido() -> str | None:
    if request.headers.get("API-Version", "1") != "1":
        return None
    socio = request.args.get("socioId", "")
    return socio if 0 < len(socio) <= 50 else None


def _oferta_a_json(oferta: OfertaSocio) -> dict[str, object]:
    contenido = oferta.contenido
    return dict(
        ofertaId=str(oferta.oferta_socio_id),
        operationId=str(oferta.operation_id),
        socioId=oferta.socio_id,
        clienteId=str(oferta.cliente_id),
        productoId=oferta.producto_id,
        solicitudId=oferta.solicitud_id,
        cotizacionId=str(oferta.cotizacion_id),
        estado="vigente" if oferta.vigente_hasta > datetime.now(UTC) else "vencida",
        calculadaEn=_iso(oferta.calculada_en),
        vigenteHasta=_iso(oferta.vigente_hasta),
        decision=contenido["decision"],
        riesgo=contenido["riesgo"],
        fuentes=contenido["fuentes"],
        variables=contenido["variables"],
        ofertas=contenido["ofertas"],
        hashContenido=oferta.hash_contenido,
    )


def _iso(fecha: datetime) -> str:
    return fecha.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _a_json(operacion: OperacionSocio) -> dict[str, object]:
    oferta = (
        operacion.oferta_hipotecaria.contenido
        if operacion.oferta_hipotecaria is not None
        else _oferta_recomendada(operacion)
    )
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
        oferta=oferta,
    )


def _oferta_recomendada(operacion: OperacionSocio) -> dict[str, object]:
    resultado = resultado_ofertas_a_json(
        _ofertas().listar(operacion.solicitud_id, operacion.cliente_id)
    )
    ofertas = resultado["ofertas"]
    if not isinstance(ofertas, list):
        raise ValueError("Respuesta de ofertas invalida.")
    oferta = next(
        (
            item
            for item in ofertas
            if isinstance(item, dict)
            and item.get("recomendada") is True
            and item.get("estado") == "disponible"
        ),
        None,
    )
    if oferta is None:
        raise ValueError("La cotizacion no produjo una oferta recomendada disponible.")
    return oferta


def _ofertas() -> GestionarOfertas:
    return cast(GestionarOfertas, current_app.extensions["gestionar_ofertas"])
