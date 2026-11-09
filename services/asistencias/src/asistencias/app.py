import hmac
import json
import logging
from typing import Any
from uuid import UUID, uuid4

from flask import Flask, Response, g, jsonify, request
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import BadRequest, UnsupportedMediaType

from asistencias.authorization import HttpResourceAuthorizer, ResourceAuthorizer
from asistencias.domain import AssistanceInput, Config, Problem, ProviderQuery
from asistencias.repository import Repository

logger = logging.getLogger(__name__)


def create_app(config: Config | None = None, authorizer: ResourceAuthorizer | None = None) -> Flask:
    active = config or Config.from_environment()
    app = Flask(__name__)
    repository = Repository(active, authorizer or HttpResourceAuthorizer(active))
    app.extensions["asistencias_repository"] = repository
    app.extensions["asistencias_engine"] = repository.engine

    @app.before_request
    def authorize() -> None:
        try:
            g.correlation_id = UUID(request.headers.get("X-Correlation-ID", ""))
        except ValueError:
            g.correlation_id = uuid4()
        g.client_id = None
        if request.path == "/health":
            return
        if request.args:
            raise Problem(400, "solicitud_invalida")
        if not hmac.compare_digest(
            request.headers.get("Authorization", "").encode(),
            f"Bearer {active.internal_token}".encode(),
        ):
            raise Problem(401, "no_autorizado")
        try:
            g.client_id = UUID(request.headers.get("X-Cliente-ID", ""))
        except ValueError as error:
            raise Problem(400, "solicitud_invalida") from error

    @app.after_request
    def private(response: Response) -> Response:
        response.headers["X-Correlation-ID"] = str(g.correlation_id)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    def body() -> Any:
        if request.content_length is None or request.content_length > 16 * 1024:
            raise Problem(413, "limite_tamano")
        return request.get_json()

    @app.get("/health")
    def health() -> Response:
        return jsonify(status="ok")

    @app.post("/v1/prestadores/consulta")
    def providers() -> Response:
        data = ProviderQuery.model_validate(body())
        return jsonify(prestadores=repository.providers(g.client_id, data, g.correlation_id))

    @app.post("/v1/asistencias")
    def register() -> tuple[Response, int]:
        try:
            operation_id = UUID(request.headers.get("Idempotency-Key", ""))
        except ValueError as error:
            raise Problem(400, "solicitud_invalida") from error
        record, status = repository.register(
            g.client_id,
            operation_id,
            AssistanceInput.model_validate(body()),
            g.correlation_id,
        )
        return jsonify(record), status

    @app.errorhandler(Problem)
    def problem(error: Problem) -> Response:
        logger.warning(
            json.dumps(
                {
                    "event": "asistencias.rechazo",
                    "code": error.code,
                    "correlationId": str(g.correlation_id),
                    "action": request.endpoint or "ruta_desconocida",
                }
            )
        )
        response = jsonify(
            type=f"https://api.solventa.co/problems/{error.code}",
            title=error.code,
            status=error.status,
            code=error.code,
            correlationId=str(g.correlation_id),
        )
        response.status_code = error.status
        response.content_type = "application/problem+json"
        if error.status == 503:
            response.headers["Retry-After"] = "5"
        return response

    @app.errorhandler(ValidationError)
    @app.errorhandler(BadRequest)
    @app.errorhandler(UnsupportedMediaType)
    def invalid(_: Exception) -> Response:
        return problem(Problem(400, "solicitud_invalida"))

    @app.errorhandler(SQLAlchemyError)
    @app.errorhandler(OSError)
    def unavailable(error: Exception) -> Response:
        logger.error(
            "Fallo de Asistencias; clase=%s correlationId=%s",
            type(error).__name__,
            g.correlation_id,
        )
        return problem(Problem(503, "servicio_no_disponible"))

    return app
