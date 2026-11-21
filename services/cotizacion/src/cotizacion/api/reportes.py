import hmac
import logging
from datetime import UTC, datetime
from typing import cast

from flask import Blueprint, Response, current_app, g, jsonify, request
from sqlalchemy.exc import SQLAlchemyError

from cotizacion.config import Config
from cotizacion.domain.reporte import (
    ReporteInconsistenteError,
    corte_desde_texto,
    huella,
    instante,
)
from cotizacion.infrastructure.persistence.repositorio_reportes import RepositorioReportePrimas

bp = Blueprint("reportes", __name__, url_prefix="/v1/reportes")
logger = logging.getLogger(__name__)


@bp.get("/primas")
def primas() -> tuple[Response, int] | Response:
    if not _authorized():
        return _respuesta(jsonify(error="no_autorizado"), 401)
    try:
        if set(request.args) != {"corte"} or len(request.args.getlist("corte")) != 1:
            raise ValueError("Se requiere un unico corte.")
        if request.get_data():
            raise ValueError("La consulta no admite cuerpo.")
        corte = corte_desde_texto(request.args["corte"], datetime.now(UTC))
    except ValueError:
        return _respuesta(jsonify(error="solicitud_invalida"), 400)
    repositorio = cast(RepositorioReportePrimas, current_app.extensions["reporte_primas"])
    try:
        registros = repositorio.primas(corte)
    except (SQLAlchemyError, ReporteInconsistenteError):
        logger.exception(
            "Reporte de primas no disponible", extra={"correlationId": g.get("correlation_id")}
        )
        return _respuesta(jsonify(error="reporte_no_disponible"), 503)
    return _respuesta(
        jsonify(
            corte=instante(corte),
            fuente="primas",
            registros=len(registros),
            huella=huella(registros),
            primas=registros,
        ),
        200,
    )


def _authorized() -> bool:
    config = cast(Config, current_app.extensions["cotizacion_config"])
    esperado = "Bearer " + config.internal_api_token
    return bool(config.internal_api_token) and hmac.compare_digest(
        request.headers.get("Authorization", ""), esperado
    )


def _respuesta(respuesta: Response, status: int) -> tuple[Response, int]:
    respuesta.headers["Cache-Control"] = "no-store"
    return respuesta, status
