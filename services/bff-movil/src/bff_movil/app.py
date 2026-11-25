from datetime import UTC, datetime
from uuid import UUID, uuid4

from flask import Flask, Response, g, request
from werkzeug.exceptions import BadRequest, UnsupportedMediaType

from bff_movil.api import (
    assistance,
    biometrics,
    claims,
    health,
    notifications,
    password_recovery,
    policies,
    policy_modifications,
    policy_renewals,
    sessions,
    users,
    verification,
)
from bff_movil.application.biometric_ports import BiometricGateway
from bff_movil.application.policy_modification_ports import PolicyModificationGateway
from bff_movil.application.policy_renewal_ports import PolicyQueryGateway, PolicyRenewalGateway
from bff_movil.application.ports import IdentityGateway, SessionStateGateway, TokenVerifier
from bff_movil.application.verification_ports import VerificationGateway
from bff_movil.config import Config
from bff_movil.domain.auth import CHANNEL
from bff_movil.domain.errors import IdentityUnavailableError
from bff_movil.infrastructure.http_identity_gateway import HttpIdentityGateway
from bff_movil.infrastructure.http_policy_modification_gateway import (
    HttpPolicyModificationGateway,
)
from bff_movil.infrastructure.http_policy_renewal_gateway import HttpPolicyRenewalGateway
from bff_movil.infrastructure.jwt_verifier import JwksKeyStore, JwtVerifier, http_jwks_fetcher
from bff_movil.problems import problem_response


def create_app(
    config: Config | None = None,
    identity_gateway: IdentityGateway | None = None,
    token_verifier: TokenVerifier | None = None,
    biometric_gateway: BiometricGateway | None = None,
    policy_renewal_gateway: PolicyRenewalGateway | None = None,
    session_state_gateway: SessionStateGateway | None = None,
    policy_query_gateway: PolicyQueryGateway | None = None,
    policy_modification_gateway: PolicyModificationGateway | None = None,
    verification_gateway: VerificationGateway | None = None,
) -> Flask:
    active_config = config or Config.from_environment()
    app = Flask(__name__)

    app.extensions["bff_config"] = active_config
    policies_gateway = HttpPolicyRenewalGateway(
        active_config.policies_service_url, active_config.internal_api_token
    )
    app.extensions["biometric_gateway"] = biometric_gateway or HttpIdentityGateway(
        active_config.identity_url, active_config.identity_timeout_seconds
    )
    app.extensions["clock"] = lambda: datetime.now(UTC)
    app.extensions["verification_gateway"] = verification_gateway or HttpIdentityGateway(
        active_config.identity_url, active_config.identity_timeout_seconds
    )
    app.extensions["session_state_gateway"] = session_state_gateway or HttpIdentityGateway(
        active_config.identity_url, active_config.identity_timeout_seconds
    )
    app.extensions["identity_gateway"] = identity_gateway or HttpIdentityGateway(
        active_config.identity_url,
        active_config.identity_timeout_seconds,
    )
    app.extensions["recovery_gateway"] = HttpIdentityGateway(
        active_config.identity_url, active_config.identity_timeout_seconds
    )
    app.extensions["token_verifier"] = token_verifier or JwtVerifier(
        JwksKeyStore(
            http_jwks_fetcher(active_config.jwks_endpoint, active_config.identity_timeout_seconds)
        ),
        issuer=active_config.jwt_issuer,
        audience=active_config.jwt_audience,
        client_id=CHANNEL,
    )
    app.extensions["policy_renewal_gateway"] = policy_renewal_gateway or policies_gateway
    app.extensions["policy_query_gateway"] = policy_query_gateway or policies_gateway
    app.extensions["policy_modification_gateway"] = (
        policy_modification_gateway
        or HttpPolicyModificationGateway(
            active_config.policies_service_url, active_config.internal_api_token
        )
    )

    app.register_blueprint(health.bp)
    app.register_blueprint(users.bp)
    app.register_blueprint(sessions.bp)
    app.register_blueprint(password_recovery.bp)
    app.register_blueprint(biometrics.bp)
    app.register_blueprint(claims.bp)
    app.register_blueprint(notifications.bp)
    app.register_blueprint(assistance.bp)
    app.register_blueprint(policies.bp)
    app.register_blueprint(policy_renewals.bp)
    app.register_blueprint(policy_modifications.bp)
    app.register_blueprint(verification.bp)

    @app.before_request
    def assign_correlation_id() -> None:
        g.correlation_id = _correlation_id(request.headers.get("X-Correlation-ID"))

    @app.after_request
    def include_correlation_id(response: Response) -> Response:
        response.headers["X-Correlation-ID"] = str(g.correlation_id)
        if request.path.startswith("/movil" + password_recovery.BASE):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
        if request.path.startswith(
            ("/movil/v1/biometria/", "/movil/v1/verificaciones-identidad")
        ) or (request.path == "/movil/v1/sesiones/biometria"):
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
        return response

    return app


def _correlation_id(candidate: str | None) -> UUID:
    if candidate:
        try:
            return UUID(candidate)
        except ValueError:
            pass
    return uuid4()
