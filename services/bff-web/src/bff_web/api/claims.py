import json
from collections.abc import Iterator
from http.client import HTTPException
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID

from flask import Blueprint, Response, current_app, g, request, stream_with_context
from pydantic import ValidationError

from bff_web.api.security import authenticate, invalid_session_response
from bff_web.domain.claims import ClaimDetail, ClaimDraft, ClaimList, ClaimPolicies
from bff_web.domain.errors import InvalidAccessTokenError
from bff_web.problems import problem_response

bp = Blueprint("claims", __name__, url_prefix="/v1/siniestros")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


@bp.after_request
def private(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    if response.status_code >= 400:
        current_app.logger.warning(
            "siniestros.rechazo canal=web status=%s correlationId=%s",
            response.status_code,
            g.correlation_id,
        )
    return response


@bp.route("", methods=["GET", "POST"])
@bp.route("/<path:tail>", methods=["GET", "POST", "PUT", "DELETE"])
def forward(tail: str = "") -> Response:
    try:
        claims = authenticate()
    except InvalidAccessTokenError:
        return invalid_session_response()
    if claims.user.role != "cliente" or claims.scope != "cliente":
        return problem_response(403, "alcance_insuficiente", "Acceso denegado", "Requiere cliente.")
    config = current_app.extensions["bff_config"]
    if not config.claims_service_url or not config.internal_api_token:
        return unavailable()
    if request.args or ".." in tail or "%" in tail or "\\" in tail:
        return problem_response(400, "solicitud_invalida", "Solicitud invalida", "Ruta invalida.")
    if request.headers.get("Transfer-Encoding"):
        return problem_response(411, "longitud_requerida", "Longitud requerida", "Indica longitud.")
    length = request.content_length
    maximum = 1024 * 1024 if request.method == "PUT" else 16 * 1024
    if length is not None and length > maximum:
        return problem_response(
            413, "limite_tamano", "Limite excedido", "Fragmento demasiado grande."
        )
    if request.method in {"POST", "PUT"} and length is None:
        if request.method == "PUT":
            return problem_response(
                411, "longitud_requerida", "Longitud requerida", "Indica longitud."
            )
        length = 0
    submitted = None
    if request.method == "POST" and not tail:
        try:
            submitted = ClaimDraft.model_validate(request.get_json())
        except ValidationError:
            return problem_response(
                400, "solicitud_invalida", "Solicitud invalida", "Revisa los datos del aviso."
            )
    headers = {
        "Authorization": f"Bearer {config.internal_api_token}",
        "X-Cliente-ID": str(claims.user.user_id),
        "X-Correlation-ID": str(g.correlation_id),
    }
    for name in ("Content-Type", "Idempotency-Key", "Upload-Offset"):
        if value := request.headers.get(name):
            headers[name] = value
    if length is not None:
        headers["Content-Length"] = str(length)
    outgoing = Request(
        config.claims_service_url
        + "/v1/siniestros"
        + (f"/{quote(tail, safe='/')}" if tail else ""),
        method=request.method,
        headers=headers,
        data=request.get_data() if submitted is not None else request.stream if length else None,
    )
    try:
        try:
            upstream = build_opener(NoRedirect).open(outgoing, timeout=180)
        except HTTPError as error:
            upstream = error
        if upstream.status == 401:
            upstream.close()
            return unavailable()
        if upstream.status == 200 and "/evidencias/" in tail and tail.endswith("/contenido"):
            try:
                size = int(upstream.headers["Content-Length"])
            except (TypeError, ValueError, KeyError):
                upstream.close()
                raise
            mime = upstream.headers.get_content_type()
            if size <= 0 or mime not in {"image/jpeg", "image/png", "video/mp4", "application/pdf"}:
                upstream.close()
                return unavailable()

            def chunks() -> Iterator[bytes]:
                with upstream:
                    while chunk := upstream.read(64 * 1024):
                        yield chunk

            response = Response(stream_with_context(chunks()), mimetype=mime)
            response.headers["Content-Length"] = str(size)
            response.headers["Content-Disposition"] = 'attachment; filename="evidencia"'
            response.call_on_close(upstream.close)
            return response
        with upstream:
            payload = upstream.read(1024 * 1024 + 1)
            if len(payload) > 1024 * 1024:
                return unavailable()
            if upstream.status == 204 and not payload:
                return Response(status=204)
            document = json.loads(payload)
            if not isinstance(document, dict):
                return unavailable()
            detail = None
            if upstream.status in {200, 201}:
                if submitted is not None:
                    detail = ClaimDetail.model_validate(document)
                    if (
                        detail.model_dump(include=set(ClaimDraft.model_fields))
                        != submitted.model_dump()
                    ):
                        raise ValueError("La confirmacion no corresponde al aviso.")
                elif request.method == "GET":
                    if not tail:
                        ClaimList.model_validate(document)
                    elif tail == "polizas":
                        ClaimPolicies.model_validate(document)
                    elif "/" not in tail and tail != "configuracion-evidencias":
                        detail = ClaimDetail.model_validate(document)
                        if detail.id != UUID(tail):
                            raise ValueError("La respuesta no corresponde al siniestro.")
            if upstream.status not in {
                200,
                201,
                400,
                401,
                403,
                404,
                405,
                409,
                411,
                413,
                415,
                422,
                503,
            }:
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
            if submitted is not None and detail is not None:
                response.headers["Location"] = f"{request.path}/{detail.id}"
            return response
    except (OSError, HTTPException, ValueError, TypeError, KeyError) as error:
        current_app.logger.error(
            "Fallo HTTP de siniestros; clase=%s correlationId=%s; no se confirma la operacion.",
            type(error).__name__,
            g.correlation_id,
        )
        return unavailable()


def unavailable() -> Response:
    response = problem_response(
        503,
        "servicio_no_disponible",
        "Servicio no disponible",
        "Consulta el estado antes de reintentar.",
    )
    response.headers["Retry-After"] = "5"
    return response
