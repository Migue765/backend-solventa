import json
from http.client import HTTPException
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from flask import Blueprint, Response, current_app, g, request

from bff_movil.api.security import authenticate, invalid_session_response
from bff_movil.domain.errors import InvalidAccessTokenError
from bff_movil.problems import problem_response

bp = Blueprint("notifications", __name__, url_prefix="/movil/v1/notificaciones")


@bp.after_request
def private(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@bp.get("")
@bp.route("/preferencias", methods=["GET", "PUT"])
def forward() -> Response:
    try:
        claims = authenticate()
    except InvalidAccessTokenError:
        return invalid_session_response()
    if claims.user.role != "cliente" or claims.scope != "cliente":
        return problem_response(403, "alcance_insuficiente", "Acceso denegado", "Requiere cliente.")
    config = current_app.extensions["bff_config"]
    if not config.notifications_service_url or not config.internal_api_token:
        return unavailable()
    if request.args:
        return problem_response(
            400, "solicitud_invalida", "Solicitud inválida", "No se admiten parámetros."
        )
    payload: bytes | None = None
    if request.method == "PUT":
        if request.content_length is None:
            return problem_response(
                411, "longitud_requerida", "Longitud requerida", "Indica longitud."
            )
        if request.content_length > 16 * 1024:
            return problem_response(
                413, "limite_tamano", "Límite excedido", "El cuerpo es demasiado grande."
            )
        payload = request.get_data(cache=False)
    outgoing = Request(
        config.notifications_service_url
        + "/v1/notificaciones"
        + ("/preferencias" if request.path.endswith("/preferencias") else ""),
        method=request.method,
        headers={
            "Authorization": f"Bearer {config.internal_api_token}",
            "X-Cliente-ID": str(claims.user.user_id),
            "X-Correlation-ID": str(g.correlation_id),
            "Content-Type": "application/json",
        },
        data=payload,
    )
    try:
        try:
            upstream = urlopen(outgoing, timeout=config.identity_timeout_seconds)
        except HTTPError as error:
            upstream = error
        with upstream:
            body = upstream.read(1024 * 1024 + 1)
            if len(body) > 1024 * 1024 or not isinstance(json.loads(body), dict):
                return unavailable()
            if upstream.status not in {200, 400, 401, 411, 413, 422, 503}:
                return unavailable()
            if upstream.status == 401:
                return unavailable()
            response = Response(
                body,
                status=upstream.status,
                content_type=(
                    "application/problem+json" if upstream.status >= 400 else "application/json"
                ),
            )
            if upstream.status == 503:
                response.headers["Retry-After"] = "5"
            return response
    except (OSError, HTTPException, ValueError, TypeError):
        current_app.logger.exception("Fallo HTTP de notificaciones.")
        return unavailable()


def unavailable() -> Response:
    response = problem_response(
        503,
        "servicio_no_disponible",
        "Servicio no disponible",
        "Las notificaciones no están disponibles temporalmente.",
    )
    response.headers["Retry-After"] = "5"
    return response
