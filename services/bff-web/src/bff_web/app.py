from datetime import UTC, datetime
from uuid import UUID, uuid4

from flask import Flask, Response, g, request
from flask_cors import CORS
from werkzeug.exceptions import BadRequest, UnsupportedMediaType

from bff_web.api import health, quotes, sessions
from bff_web.application.auth_service import AuthService
from bff_web.application.quote_ports import QuoteGateway
from bff_web.config import Config
from bff_web.infrastructure.development_credentials import DevelopmentCredentialProvider
from bff_web.infrastructure.http_quote_gateway import HttpQuoteGateway
from bff_web.infrastructure.memory_attempt_limiter import InMemoryLoginAttemptLimiter
from bff_web.infrastructure.memory_sessions import InMemorySessionRepository
from bff_web.problems import problem_response


def create_app(
    config: Config | None = None,
    quote_gateway: QuoteGateway | None = None,
) -> Flask:
    active_config = config or Config.from_environment()
    app = Flask(__name__)
    CORS(
        app,
        origins=active_config.cors_allowed_origins,
        supports_credentials=True,
    )

    app.extensions["bff_config"] = active_config
    app.extensions["auth_service"] = AuthService(
        credentials=DevelopmentCredentialProvider(active_config.credentials),
        sessions=InMemorySessionRepository(),
        attempt_limiter=InMemoryLoginAttemptLimiter(
            active_config.max_attempts,
            active_config.attempt_window,
        ),
        clock=lambda: datetime.now(UTC),
        session_duration=active_config.session_duration,
    )
    app.extensions["quote_gateway"] = quote_gateway or HttpQuoteGateway(
        active_config.quote_service_url
    )

    app.register_blueprint(health.bp)
    app.register_blueprint(sessions.bp)
    app.register_blueprint(quotes.bp)

    @app.before_request
    def assign_correlation_id() -> None:
        g.correlation_id = _correlation_id(request.headers.get("X-Correlation-ID"))

    @app.after_request
    def include_correlation_id(response: Response) -> Response:
        response.headers["X-Correlation-ID"] = str(g.correlation_id)
        return response

    @app.errorhandler(BadRequest)
    @app.errorhandler(UnsupportedMediaType)
    def bad_request(_: BadRequest | UnsupportedMediaType) -> Response:
        return problem_response(
            status=400,
            code="solicitud_invalida",
            title="Solicitud inválida",
            detail="El cuerpo de la solicitud no contiene JSON válido.",
        )

    return app


def _correlation_id(candidate: str | None) -> UUID:
    if candidate:
        try:
            return UUID(candidate)
        except ValueError:
            pass
    return uuid4()
