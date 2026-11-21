from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from flask import Blueprint, Response, g, jsonify, request

from polizas.domain.documento import (
    ConfirmacionDocumento,
    DocumentoDesactualizado,
    hash_contenido,
)
from polizas.infrastructure.documentos import RepositorioDocumentos


def documentos_blueprint(
    repositorio: RepositorioDocumentos,
    reloj: Callable[[], datetime],
) -> Blueprint:
    bp = Blueprint("documentos", __name__, url_prefix="/v1/clientes/me/polizas")

    @bp.get("/<uuid:poliza_id>/documento")
    def consultar(poliza_id: UUID) -> Response:
        if request.args or request.get_data():
            raise ValueError("La consulta no admite parametros ni cuerpo.")
        return jsonify(repositorio.consultar(poliza_id, _cliente(), reloj()))

    @bp.get("/<uuid:poliza_id>/documentos/<uuid:documento_id>/descarga")
    def descargar(poliza_id: UUID, documento_id: UUID) -> Response:
        if request.args or request.get_data():
            raise ValueError("La descarga no admite parametros ni cuerpo.")
        archivo = repositorio.descargar(poliza_id, _cliente(), documento_id)
        response = Response(archivo.encode("utf-8"), content_type="text/html; charset=utf-8")
        response.headers["Content-Disposition"] = (
            f'attachment; filename="poliza-{poliza_id}-{documento_id}-simulada.html"'
        )
        response.headers["X-Content-SHA256"] = hash_contenido(archivo)
        return response

    @bp.post("/<uuid:poliza_id>/aceptaciones")
    def aceptar(poliza_id: UUID) -> tuple[Response, int]:
        if request.args:
            raise ValueError("La aceptacion no admite parametros.")
        confirmacion = ConfirmacionDocumento.model_validate(request.get_json())
        resultado, nueva = repositorio.aceptar(
            poliza_id,
            _cliente(),
            UUID(request.headers.get("Idempotency-Key", "")),
            confirmacion,
            str(g.correlation_id),
            reloj(),
        )
        return jsonify(resultado), 201 if nueva else 200

    @bp.errorhandler(DocumentoDesactualizado)
    def desactualizado(_: DocumentoDesactualizado) -> tuple[Response, int]:
        return jsonify(error="documento_desactualizado", correlationId=g.correlation_id), 409

    @bp.after_request
    def privado(response: Response) -> Response:
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; base-uri 'none'; form-action 'none'; "
            "frame-ancestors 'none'; sandbox"
        )
        return response

    return bp


def _cliente() -> UUID:
    return UUID(request.headers.get("X-Cliente-ID", ""))
