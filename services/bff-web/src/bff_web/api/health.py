from flask import Blueprint, Response, jsonify

bp = Blueprint("health", __name__)


@bp.get("/ping")
def ping() -> Response:
    return jsonify(status="ok", service="bff-web")
