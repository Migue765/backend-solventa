from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from flask import Blueprint, Response, current_app, g, jsonify, request

from polizas.domain.cumplimiento import ErrorCumplimiento, GuardarReporte, SolicitudIntercambio
from polizas.infrastructure.cumplimiento import RepositorioCumplimiento


def cumplimiento_blueprint(
    repositorio: RepositorioCumplimiento, reloj: Callable[[], datetime]
) -> Blueprint:
    bp = Blueprint("cumplimiento", __name__, url_prefix="/internal/v1/cumplimiento")

    @bp.before_request
    def validar() -> None:
        request.max_content_length = 12 * 1024 * 1024
        if request.args or request.headers.get("X-Actor-Rol") != "asesor":
            raise ErrorCumplimiento("acceso_denegado", 403)
        UUID(request.headers.get("X-Actor-ID", ""))
        if request.method == "GET" and request.get_data():
            raise ValueError("La consulta no admite cuerpo.")

    @bp.post("/reportes")
    def guardar() -> tuple[Response, int]:
        solicitud = GuardarReporte.model_validate(request.get_json())
        resultado, nuevo = repositorio.guardar(
            _actor(), _clave(), solicitud, str(g.correlation_id), reloj()
        )
        return jsonify(resultado), 201 if nuevo else 200

    @bp.get("/reportes/operaciones/<uuid:clave>")
    def recuperar(clave: UUID) -> Response:
        return jsonify(repositorio.recuperar(_actor(), clave, str(g.correlation_id), reloj()))

    @bp.get("/reportes/<uuid:recurso>")
    @bp.get("/reportes/<uuid:recurso>/descarga")
    def reporte(recurso: UUID) -> Response:
        accion = "descarga" if request.path.endswith("/descarga") else "consulta"
        return jsonify(
            repositorio.consultar(
                _actor(), recurso, "reportes", accion, str(g.correlation_id), reloj()
            )
        )

    @bp.post("/intercambios")
    def intercambiar() -> tuple[Response, int]:
        solicitud = SolicitudIntercambio.model_validate(request.get_json())
        resultado, nuevo = repositorio.intercambiar(
            _actor(), _clave(), solicitud, str(g.correlation_id), reloj()
        )
        return jsonify(resultado), 201 if nuevo else 200

    @bp.get("/intercambios/<uuid:recurso>")
    def intercambio(recurso: UUID) -> Response:
        return jsonify(
            repositorio.consultar(
                _actor(), recurso, "intercambios", "consulta", str(g.correlation_id), reloj()
            )
        )

    @bp.errorhandler(ErrorCumplimiento)
    def error_cumplimiento(error: ErrorCumplimiento) -> tuple[Response, int]:
        return jsonify(error=error.codigo, correlationId=g.correlation_id), error.status

    @bp.after_request
    def auditar(response: Response) -> Response:
        if response.status_code >= 400:
            current_app.logger.warning(
                "cumplimiento.rechazo status=%s correlationId=%s",
                response.status_code,
                g.correlation_id,
            )
        return response

    return bp


def _actor() -> UUID:
    return UUID(request.headers.get("X-Actor-ID", ""))


def _clave() -> UUID:
    return UUID(request.headers.get("Idempotency-Key", ""))
