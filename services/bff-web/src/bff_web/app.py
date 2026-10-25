from datetime import UTC, datetime
from uuid import UUID, uuid4

from flask import Flask, Response, g, request
from werkzeug.exceptions import BadRequest, UnsupportedMediaType

from bff_web.api import (
    client_information,
    consents,
    health,
    mortgage_profiling,
    partner_quotes,
    quotes,
    sessions,
    users,
)
from bff_web.application.client_information_ports import ClientInformationGateway
from bff_web.application.mortgage_ports import MortgageProfilingGateway
from bff_web.application.ports import IdentityGateway, TokenVerifier
from bff_web.application.profiling_ports import ProfilingGateway
from bff_web.application.quote_ports import QuoteGateway
from bff_web.config import Config
from bff_web.domain.auth import CHANNEL
from bff_web.domain.errors import IdentityUnavailableError
from bff_web.domain.profiling import ProfilingError
from bff_web.infrastructure.http_client_information_gateway import (
    HttpClientInformationGateway,
)
from bff_web.infrastructure.http_identity_gateway import HttpIdentityGateway
from bff_web.infrastructure.http_mortgage_profiling_gateway import (
    HttpMortgageProfilingGateway,
)
from bff_web.infrastructure.http_partner_quote_gateway import HttpPartnerQuoteGateway
from bff_web.infrastructure.http_profiling_gateway import HttpProfilingGateway
from bff_web.infrastructure.http_quote_gateway import HttpQuoteGateway
from bff_web.infrastructure.jwt_verifier import JwksKeyStore, JwtVerifier, http_jwks_fetcher
from bff_web.problems import problem_response


def create_app(
    config: Config | None = None,
    quote_gateway: QuoteGateway | None = None,
    identity_gateway: IdentityGateway | None = None,
    token_verifier: TokenVerifier | None = None,
    profiling_gateway: ProfilingGateway | None = None,
    mortgage_profiling_gateway: MortgageProfilingGateway | None = None,
    client_information_gateway: ClientInformationGateway | None = None,
) -> Flask:
    active_config = config or Config.from_environment()
    app = Flask(__name__)

    app.extensions["bff_config"] = active_config
    app.extensions["clock"] = lambda: datetime.now(UTC)
    app.extensions["identity_gateway"] = identity_gateway or HttpIdentityGateway(
        active_config.identity_url,
        active_config.identity_timeout_seconds,
        internal_api_token=active_config.internal_api_token,
    )
    app.extensions["token_verifier"] = token_verifier or JwtVerifier(
        JwksKeyStore(
            http_jwks_fetcher(active_config.jwks_endpoint, active_config.identity_timeout_seconds)
        ),
        issuer=active_config.jwt_issuer,
        audience=active_config.jwt_audience,
        client_id=CHANNEL,
    )
    app.extensions["quote_gateway"] = quote_gateway or HttpQuoteGateway(
        active_config.quote_service_url,
        internal_api_token=active_config.internal_api_token,
    )
    app.extensions["profiling_gateway"] = profiling_gateway or HttpProfilingGateway(
        active_config.identity_url,
        active_config.quote_service_url,
        active_config.internal_api_token,
    )
    app.extensions["mortgage_profiling_gateway"] = (
        mortgage_profiling_gateway
        or HttpMortgageProfilingGateway(
            active_config.quote_service_url,
            active_config.internal_api_token,
        )
    )
    app.extensions["partner_quote_gateway"] = HttpPartnerQuoteGateway(
        active_config.quote_service_url, active_config.internal_api_token
    )
    app.extensions["client_information_gateway"] = (
        client_information_gateway
        or HttpClientInformationGateway(
            active_config.profiling_service_url,
            active_config.internal_api_token,
        )
    )
    app.register_error_handler(ProfilingError, consents.profiling_error)

    app.register_blueprint(health.bp)
    app.register_blueprint(users.bp)
    app.register_blueprint(sessions.bp)
    app.register_blueprint(consents.bp)
    app.register_blueprint(quotes.bp)
    app.register_blueprint(mortgage_profiling.bp)
    app.register_blueprint(partner_quotes.bp)
    app.register_blueprint(client_information.bp)

    @app.before_request
    def assign_correlation_id() -> None:
        g.correlation_id = _correlation_id(request.headers.get("X-Correlation-ID"))

    @app.after_request
    def include_correlation_id(response: Response) -> Response:
        response.headers["X-Correlation-ID"] = str(g.correlation_id)
        if request.path.startswith("/v1/sesiones"):
            response.headers["Cache-Control"] = "no-store"
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

    @app.errorhandler(IdentityUnavailableError)
    def identity_unavailable(_: IdentityUnavailableError) -> Response:
        response = problem_response(
            status=503,
            code="servicio_no_disponible",
            title="Servicio no disponible",
            detail="Intenta nuevamente en unos minutos.",
        )
        response.headers["Retry-After"] = "5"
        response.headers["Cache-Control"] = "no-store"
        return response

    return app


def _correlation_id(candidate: str | None) -> UUID:
    if candidate:
        try:
            return UUID(candidate)
        except ValueError:
            pass
    return uuid4()
