import json
from collections.abc import Iterator
from contextlib import closing
from typing import cast
from uuid import UUID

from flask import Blueprint, Response, current_app, g, jsonify, request, stream_with_context
from pydantic import ValidationError

from bff_web.api.security import authenticate, internal_authorized, invalid_session_response
from bff_web.domain.claim_review import ClaimReviewError, ReviewInput, ReviewQueue
from bff_web.domain.errors import InvalidAccessTokenError
from bff_web.infrastructure.http_claim_review_gateway import HttpClaimReviewGateway
from bff_web.problems import problem_response

bp = Blueprint("claim_reviews", __name__, url_prefix="/v1/administracion")


@bp.before_request
def authorize() -> Response | None:
    try:
        claims = authenticate()
    except InvalidAccessTokenError:
        return invalid_session_response()
    if not internal_authorized(claims, "siniestros:revisar", admin=True):
        return problem_response(
            403,
            "alcance_insuficiente",
            "Acceso denegado",
            "Requiere personal de revision autorizado.",
        )
    if claims.session_id is None or claims.user.partner_ids:
        return invalid_session_response()
    g.review_actor = claims.user.user_id
    request.max_content_length = 16 * 1024
    if request.args or (request.method == "GET" and request.get_data()):
        return invalid_request()
    return None


@bp.after_request
def audit(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    details = {
        "event": "siniestros.revision_web",
        "actorId": str(g.review_actor) if "review_actor" in g else None,
        "endpoint": request.endpoint,
        "status": response.status_code,
        "correlationId": str(g.correlation_id),
    }
    if response.status_code >= 400:
        current_app.logger.warning(json.dumps(details))
    else:
        current_app.logger.info(json.dumps(details))
    return response


@bp.errorhandler(ClaimReviewError)
def review_error(error: ClaimReviewError) -> Response:
    response = problem_response(
        error.status,
        error.code,
        "Revision no completada",
        "Consulta el estado antes de reintentar.",
    )
    if error.status == 503:
        response.headers["Retry-After"] = "5"
    return response


def invalid_request() -> Response:
    return problem_response(
        400, "solicitud_invalida", "Solicitud invalida", "Revisa los datos de la revision."
    )


def gateway() -> HttpClaimReviewGateway:
    return cast(HttpClaimReviewGateway, current_app.extensions["claim_review_gateway"])


@bp.get("/colas-revision/<queue>/asignaciones")
def assignments(queue: str) -> Response:
    if queue not in {"revision", "inspeccion", "fraude"}:
        return invalid_request()
    result = gateway().assignments(cast(ReviewQueue, queue), g.review_actor, str(g.correlation_id))
    return jsonify(result.model_dump(mode="json", exclude_unset=True))


@bp.get("/siniestros/<uuid:claim_id>")
def detail(claim_id: UUID) -> Response:
    result = gateway().detail(claim_id, g.review_actor, str(g.correlation_id))
    return jsonify(result.model_dump(mode="json", exclude_unset=True))


@bp.get("/siniestros/<uuid:claim_id>/decisiones")
def decisions(claim_id: UUID) -> Response:
    result = gateway().decisions(claim_id, g.review_actor, str(g.correlation_id))
    return jsonify(result.model_dump(mode="json", exclude_unset=True))


@bp.post("/siniestros/<uuid:claim_id>/decisiones/revision")
def review(claim_id: UUID) -> Response:
    try:
        operation = UUID(request.headers.get("Idempotency-Key", ""))
        submitted = ReviewInput.model_validate(request.get_json())
    except (ValueError, ValidationError):
        return invalid_request()
    result, status = gateway().review(
        claim_id, operation, g.review_actor, submitted, str(g.correlation_id)
    )
    response = jsonify(result.model_dump(mode="json", exclude_unset=True))
    response.status_code = status
    return response


@bp.get("/siniestros/<uuid:claim_id>/evidencias/<uuid:evidence_id>/contenido")
def content(claim_id: UUID, evidence_id: UUID) -> Response:
    result = gateway().content(claim_id, evidence_id, g.review_actor, str(g.correlation_id))

    def chunks() -> Iterator[bytes]:
        try:
            with closing(result.stream):
                remaining = result.size
                while remaining:
                    chunk = result.stream.read(min(64 * 1024, remaining))
                    if not chunk:
                        raise OSError("Contenido de evidencia truncado.")
                    remaining -= len(chunk)
                    yield chunk
        except OSError:
            current_app.logger.error(
                "Evidencia incompleta en revision; correlationId=%s", g.correlation_id
            )
            raise

    response = Response(stream_with_context(chunks()), mimetype=result.mime)
    response.headers["Content-Length"] = str(result.size)
    response.headers["Content-Disposition"] = f'attachment; filename="{evidence_id}"'
    response.call_on_close(result.stream.close)
    return response
