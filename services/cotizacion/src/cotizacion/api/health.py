from flask import Blueprint, current_app, jsonify

bp = Blueprint("health", __name__)


@bp.get("/ping")
def ping():
    conciliador = current_app.extensions.get("conciliador_emision")
    if conciliador is not None and not conciliador.activo:
        return jsonify(status="error", componente="conciliacion_emision"), 503
    return jsonify(status="ok")
