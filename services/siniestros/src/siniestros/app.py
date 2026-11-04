import hmac
import json
import logging
from typing import Any
from uuid import UUID, uuid4

from flask import Flask, Response, g, jsonify, request, send_file
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import HTTPException

from siniestros.domain import ClaimInput, Config, Problem, UploadInput
from siniestros.policies import policies
from siniestros.repository import Repository

logger = logging.getLogger(__name__)


def create_app(config: Config | None = None) -> Flask:
    config = config or Config.from_environment()
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = max(64 * 1024, config.max_chunk)
    repo = Repository(config)
    app.extensions["siniestros_repository"] = repo
    app.extensions["siniestros_engine"] = repo.engine

    @app.before_request
    def authorize() -> None:
        try:
            g.correlation_id = str(UUID(request.headers.get("X-Correlation-ID", "")))
        except ValueError:
            g.correlation_id = str(uuid4())
        g.owner = None
        if request.path == "/health":
            return
        if not hmac.compare_digest(
            request.headers.get("Authorization", "").encode(),
            f"Bearer {config.internal_token}".encode(),
        ):
            raise Problem(401, "no_autorizado")
        try:
            g.owner = str(UUID(request.headers.get("X-Cliente-ID", "")))
        except ValueError as error:
            raise Problem(400, "solicitud_invalida") from error
        if request.args:
            raise Problem(400, "solicitud_invalida")

    @app.after_request
    def headers(response: Response) -> Response:
        response.headers["X-Correlation-ID"] = g.correlation_id
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    def body(maximum: int = 16 * 1024) -> Any:
        if request.content_length is None or request.content_length > maximum:
            raise Problem(413, "limite_tamano")
        return request.get_json()

    def empty() -> None:
        if request.content_length not in (None, 0) or request.stream.read(1):
            raise Problem(400, "solicitud_invalida")

    def operation() -> str:
        try:
            return str(UUID(request.headers.get("Idempotency-Key", "")))
        except ValueError as error:
            raise Problem(400, "solicitud_invalida") from error

    @app.get("/health")
    def health() -> Response:
        return jsonify(status="ok")

    @app.get("/v1/siniestros/configuracion-evidencias")
    def configuration() -> Response:
        empty()
        return jsonify(config.public())

    @app.get("/v1/siniestros/polizas")
    def list_policies() -> Response:
        empty()
        return jsonify(polizas=policies(config, g.owner, g.correlation_id))

    @app.get("/v1/siniestros")
    def list_claims() -> Response:
        empty()
        return jsonify(siniestros=repo.list_claims(g.owner, g.correlation_id))

    @app.post("/v1/siniestros")
    def create_claim() -> tuple[Response, int]:
        record, status = repo.create_claim(
            g.owner, operation(), ClaimInput.model_validate(body(64 * 1024)), g.correlation_id
        )
        return jsonify(record), status

    @app.get("/v1/siniestros/<uuid:claim_id>")
    def get_claim(claim_id: UUID) -> Response:
        empty()
        return jsonify(repo.get_claim(g.owner, str(claim_id), g.correlation_id))

    @app.post("/v1/siniestros/<uuid:claim_id>/cargas")
    def start(claim_id: UUID) -> tuple[Response, int]:
        data = body()
        if isinstance(data, dict) and data.get("mimeType") not in config.formats:
            raise Problem(415, "formato_no_permitido")
        record, status = repo.start(
            g.owner, str(claim_id), operation(), UploadInput.model_validate(data), g.correlation_id
        )
        return jsonify(record), status

    @app.get("/v1/siniestros/<uuid:claim_id>/cargas/<uuid:upload_id>")
    def get_upload(claim_id: UUID, upload_id: UUID) -> Response:
        empty()
        return jsonify(repo.get_upload(g.owner, str(claim_id), str(upload_id)))

    @app.put("/v1/siniestros/<uuid:claim_id>/cargas/<uuid:upload_id>/contenido")
    def append(claim_id: UUID, upload_id: UUID) -> Response:
        if request.mimetype != "application/octet-stream":
            raise Problem(415, "formato_no_permitido")
        length = request.content_length
        if length is None:
            raise Problem(411, "longitud_requerida")
        raw_offset = request.headers.get("Upload-Offset", "")
        if not raw_offset.isascii() or not raw_offset.isdecimal():
            raise Problem(400, "solicitud_invalida")
        return jsonify(
            repo.append(
                g.owner,
                str(claim_id),
                str(upload_id),
                int(raw_offset),
                length,
                request.stream,
                g.correlation_id,
            )
        )

    @app.post("/v1/siniestros/<uuid:claim_id>/cargas/<uuid:upload_id>/confirmacion")
    def confirm(claim_id: UUID, upload_id: UUID) -> Response:
        empty()
        return jsonify(repo.confirm(g.owner, str(claim_id), str(upload_id), g.correlation_id))

    @app.delete("/v1/siniestros/<uuid:claim_id>/cargas/<uuid:upload_id>")
    def cancel(claim_id: UUID, upload_id: UUID) -> Response:
        empty()
        repo.cancel(g.owner, str(claim_id), str(upload_id), g.correlation_id)
        return Response(status=204)

    @app.get("/v1/siniestros/<uuid:claim_id>/evidencias")
    def evidence(claim_id: UUID) -> Response:
        empty()
        return jsonify(evidencias=repo.list_evidence(g.owner, str(claim_id)))

    @app.get("/v1/siniestros/<uuid:claim_id>/evidencias/<uuid:upload_id>/contenido")
    def content(claim_id: UUID, upload_id: UUID) -> Response:
        empty()
        path, mime, size = repo.content(g.owner, str(claim_id), str(upload_id), g.correlation_id)
        response = send_file(
            path,
            mimetype=mime,
            as_attachment=True,
            download_name=str(upload_id),
            conditional=False,
            etag=False,
        )
        response.headers["Content-Length"] = str(size)
        return response

    @app.errorhandler(Problem)
    def problem(error: Problem) -> Response:
        details = {
            "event": "siniestros.rechazo",
            "code": error.code,
            "correlationId": g.correlation_id,
            "action": request.endpoint or "ruta_desconocida",
        }
        logger.warning(json.dumps(details))
        try:
            repo.failure(g.get("owner"), details["action"], error.code, g.correlation_id)
        except SQLAlchemyError:
            logger.error(
                "No se pudo persistir la auditoria de rechazo; correlationId=%s", g.correlation_id
            )
        response = jsonify(
            type=f"https://api.solventa.co/problems/{error.code}",
            title=error.code,
            status=error.status,
            code=error.code,
            correlationId=g.correlation_id,
        )
        response.status_code = error.status
        response.content_type = "application/problem+json"
        if error.status == 503:
            response.headers["Retry-After"] = "5"
        return response

    @app.errorhandler(ValidationError)
    def invalid(_: ValidationError) -> Response:
        return problem(Problem(400, "solicitud_invalida"))

    @app.errorhandler(HTTPException)
    def http_error(error: HTTPException) -> Response:
        return problem(
            Problem(
                error.code or 400, "limite_tamano" if error.code == 413 else "solicitud_invalida"
            )
        )

    @app.errorhandler(SQLAlchemyError)
    @app.errorhandler(OSError)
    def unavailable(error: Exception) -> Response:
        logger.error(
            "Fallo de almacenamiento o dependencia; clase=%s correlationId=%s",
            type(error).__name__,
            g.correlation_id,
        )
        return problem(Problem(503, "servicio_no_disponible"))

    return app
