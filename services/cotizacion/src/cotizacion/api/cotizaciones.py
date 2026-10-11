from flask import Blueprint, current_app, jsonify, request
from pydantic import ValidationError

from cotizacion.api.schemas import CrearCotizacionRequest, cotizacion_a_json
from cotizacion.application.crear_cotizacion import CrearCotizacion

bp = Blueprint("cotizaciones", __name__, url_prefix="/v1/cotizaciones")


@bp.post("")
def crear_cotizacion():
    cuerpo = request.get_json(silent=True)
    if cuerpo is None:
        return jsonify(error="solicitud_invalida", mensaje="El cuerpo debe ser un JSON válido."), 400
    try:
        solicitud = CrearCotizacionRequest.model_validate(cuerpo)
    except ValidationError as e:
        detalles = e.errors(include_url=False, include_context=False, include_input=False)
        return jsonify(error="solicitud_invalida", detalles=detalles), 400

    caso_de_uso: CrearCotizacion = current_app.extensions["crear_cotizacion"]
    cotizacion = caso_de_uso.ejecutar(solicitud.a_comando())
    return jsonify(cotizacion_a_json(cotizacion)), 201
