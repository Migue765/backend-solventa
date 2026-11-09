import json
from http.client import HTTPException
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from flask import Blueprint, Response, current_app, g, request

from bff_movil.api.security import authenticate, invalid_session_response
from bff_movil.domain.errors import InvalidAccessTokenError
from bff_movil.problems import problem_response

bp = Blueprint("assistance", __name__, url_prefix="/movil/v1")


@bp.after_request
def private(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@bp.post("/prestadores/consulta")
def providers() -> Response:
    return forward("/v1/prestadores/consulta", include_idempotency=False)


@bp.post("/asistencias")
def register() -> Response:
    return forward("/v1/asistencias", include_idempotency=True)


def forward(path: str, include_idempotency: bool) -> Response:
    try:
        claims = authenticate()
    except InvalidAccessTokenError:
        return invalid_session_response()
    if claims.user.role != "cliente" or claims.scope != "cliente":
        return problem_response(403, "alcance_insuficiente", "Acceso denegado", "Requiere cliente.")
    config = current_app.extensions["bff_config"]
    if not config.assistance_service_url or not config.internal_api_token:
        return unavailable()
    if request.args or request.content_length is None or request.content_length > 16 * 1024:
        return problem_response(
            400 if request.args or request.content_length is None else 413,
            "solicitud_invalida"
            if request.args or request.content_length is None
            else "limite_tamano",
            "Solicitud invalida",
            "El cuerpo no cumple el contrato.",
        )
    headers = {
        "Authorization": f"Bearer {config.internal_api_token}",
        "X-Cliente-ID": str(claims.user.user_id),
        "X-Correlation-ID": str(g.correlation_id),
        "Content-Type": request.content_type or "application/json",
        "Content-Length": str(request.content_length),
    }
    if include_idempotency and (operation := request.headers.get("Idempotency-Key")):
        headers["Idempotency-Key"] = operation
    outgoing = Request(
        config.assistance_service_url + path,
        method="POST",
        headers=headers,
        data=request.get_data(cache=False),
    )
    try:
        try:
            upstream = urlopen(outgoing, timeout=config.identity_timeout_seconds)
        except HTTPError as error:
            upstream = error
        with upstream:
            payload = upstream.read(1024 * 1024 + 1)
            if len(payload) > 1024 * 1024 or not isinstance(json.loads(payload), dict):
                return unavailable()
            if upstream.status not in {200, 201, 400, 401, 404, 409, 413, 422, 503}:
                return unavailable()
            if upstream.status == 401:
                return unavailable()
            response = Response(
                payload,
                status=upstream.status,
                content_type=(
                    "application/problem+json" if upstream.status >= 400 else "application/json"
                ),
            )
            if upstream.status == 503:
                response.headers["Retry-After"] = "5"
            return response
    except (OSError, HTTPException, ValueError, TypeError):
        current_app.logger.exception("Fallo HTTP de asistencias; no se confirma la operacion.")
        return unavailable()


def unavailable() -> Response:
    response = problem_response(
        503,
        "servicio_no_disponible",
        "Servicio no disponible",
        "No se confirmo ninguna asistencia.",
    )
    response.headers["Retry-After"] = "5"
    return response
