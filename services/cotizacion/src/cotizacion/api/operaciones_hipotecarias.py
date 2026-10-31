import hmac
from typing import cast
from uuid import UUID

from flask import Blueprint, Response, current_app, jsonify, request
from pydantic import TypeAdapter, ValidationError

from cotizacion.api.schemas import IniciarOperacionHipotecariaRequest
from cotizacion.application.iniciar_operacion_hipotecaria import IniciarOperacionHipotecaria
from cotizacion.config import Config
from cotizacion.domain.operacion_hipotecaria import OperacionHipotecaria

bp = Blueprint("operaciones_hipotecarias", __name__, url_prefix="/v1")
UUID_ADAPTER: TypeAdapter[UUID] = TypeAdapter(UUID)


@bp.post("/perfilamientos-hipotecarios")
def iniciar() -> tuple[Response, int] | Response:
    if not _authorized():
        return jsonify(error="no_autorizado"), 401
    if request.headers.get("API-Version", "1") != "1":
        return jsonify(error="version_no_soportada"), 400
    try:
        payload = IniciarOperacionHipotecariaRequest.model_validate(request.get_json(silent=False))
        idempotency_key = UUID_ADAPTER.validate_python(request.headers.get("Idempotency-Key"))
    except (ValidationError, TypeError):
        return jsonify(error="solicitud_invalida"), 400
    if idempotency_key != payload.operation_id:
        return jsonify(error="solicitud_invalida"), 400

    caso_uso = cast(
        IniciarOperacionHipotecaria,
        current_app.extensions["iniciar_operacion_hipotecaria"],
    )
    operacion = caso_uso.ejecutar(payload.a_comando())
    response = jsonify(_a_json(operacion))
    response.headers["Cache-Control"] = "no-store"
    return response, 201


def _authorized() -> bool:
    config = cast(Config, current_app.extensions["cotizacion_config"])
    return bool(config.internal_api_token) and hmac.compare_digest(
        request.headers.get("Authorization", ""), f"Bearer {config.internal_api_token}"
    )


def _a_json(operacion: OperacionHipotecaria) -> dict[str, object]:
    return {
        "operationId": str(operacion.operation_id),
        "socioId": operacion.socio_id,
        "asesorId": str(operacion.asesor_id),
        "clienteId": str(operacion.cliente_id),
        "productoId": "mortgage-life",
        "estado": operacion.estado,
        "perfilamientoReferencia": operacion.perfilamiento_referencia,
        "solicitudId": operacion.solicitud_id,
        "cotizacionId": str(operacion.cotizacion_id),
        "recibidaEn": operacion.creada_en.isoformat().replace("+00:00", "Z"),
    }
