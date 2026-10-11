from flask import Blueprint, jsonify

bp = Blueprint("health", __name__)


@bp.get("/ping")
def ping():
    return jsonify(status="ok")
