from datetime import timedelta
from uuid import NAMESPACE_URL, uuid5

from flask import Blueprint, Response, jsonify

from bff_web.api.identity import clock
from bff_web.api.security import authenticate, invalid_session_response
from bff_web.domain.errors import InvalidAccessTokenError

bp = Blueprint("consents", __name__, url_prefix="/v1/consentimientos")


@bp.get("/perfilamiento")
def profiling_consent() -> Response:
    try:
        user = authenticate().user
    except InvalidAccessTokenError:
        return invalid_session_response()

    now = clock()()
    response = jsonify(
        consentimientoId=str(uuid5(NAMESPACE_URL, f"solventa:consentimiento-mock:{user.user_id}")),
        clienteId=str(user.user_id),
        version=1,
        estado="autorizado",
        autorizado=True,
        finalidad="cotizacion",
        alcances=["open_finance", "open_data"],
        efectivaEn=now.isoformat().replace("+00:00", "Z"),
        vigenteHasta=(now + timedelta(days=30)).isoformat().replace("+00:00", "Z"),
        simulada=True,
    )
    response.headers["Cache-Control"] = "no-store"
    return response
