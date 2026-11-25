from collections.abc import Callable
from datetime import UTC, datetime
from functools import partial
from uuid import UUID, uuid4

import click
from flask import Flask, Response, g, request
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import BadRequest, UnsupportedMediaType

from identidad.api import (
    advisors,
    biometrics,
    consents,
    health,
    notification_mail,
    oauth,
    password_recovery,
    session_state,
    users,
    verification,
    wellknown,
)
from identidad.api.schemas import RegisterRequest
from identidad.application.biometric_service import BiometricService
from identidad.application.password_recovery_service import PasswordRecoveryService
from identidad.application.ports import LoginAttemptLimiter, RecoveryMailSender
from identidad.application.registration_service import RegistrationService
from identidad.application.token_service import TokenService
from identidad.config import Config
from identidad.domain.auth import User
from identidad.domain.errors import EmailAlreadyRegisteredError, TokenIssuanceError
from identidad.infrastructure.biometric_crypto import P256BiometricCrypto
from identidad.infrastructure.biometric_limiter import RedisBiometricLimiter
from identidad.infrastructure.encrypted_login import EncryptedLogin
from identidad.infrastructure.jwt_issuer import RsaJwtIssuer
from identidad.infrastructure.memory_attempt_limiter import InMemoryLoginAttemptLimiter
from identidad.infrastructure.passwords import hash_password
from identidad.infrastructure.persistence.biometric_repository import SqlBiometricRepository
from identidad.infrastructure.persistence.consent_repository import SqlConsentRepository
from identidad.infrastructure.persistence.database import create_sessions
from identidad.infrastructure.persistence.password_recovery_repository import (
    SqlPasswordRecoveryRepository,
)
from identidad.infrastructure.persistence.refresh_token_repository import (
    SqlRefreshTokenRepository,
)
from identidad.infrastructure.persistence.user_repository import SqlUserRepository
from identidad.infrastructure.persistence.verification_repository import SqlVerificationRepository
from identidad.infrastructure.redis_attempt_limiter import RedisLoginAttemptLimiter
from identidad.infrastructure.ses_recovery_mail import SesRecoveryMailSender
from identidad.problems import problem_response


def create_app(
    config: Config | None = None,
    clock: Callable[[], datetime] | None = None,
    recovery_mail_sender: RecoveryMailSender | None = None,
) -> Flask:
    active_config = config or Config.from_environment()
    active_clock = clock or (lambda: datetime.now(UTC))
    app = Flask(__name__)

    attempt_limiter: LoginAttemptLimiter = InMemoryLoginAttemptLimiter(
        active_config.max_attempts,
        active_config.attempt_window,
    )
    if active_config.redis_url:
        redis_client = Redis.from_url(
            active_config.redis_url,
            decode_responses=True,
            socket_timeout=2,
            socket_connect_timeout=2,
        )
        attempt_limiter = RedisLoginAttemptLimiter(
            redis_client,
            active_config.max_attempts,
            active_config.attempt_window,
        )
        if active_config.encrypted_login_enabled:
            app.extensions["encrypted_login"] = EncryptedLogin(
                active_config.login_encryption_private_key,
                active_config.jwt_private_key,
                redis_client,
                active_clock,
            )

    sessions = create_sessions(
        active_config.database_url,
        active_config.db_schema,
        active_config.db_pool_size,
        active_config.db_max_overflow,
    )
    user_repository = SqlUserRepository(sessions)
    jwt_issuer = RsaJwtIssuer(
        active_config.jwt_private_key,
        active_config.jwt_issuer,
        active_config.jwt_audience,
    )

    app.extensions["identidad_config"] = active_config
    app.extensions["clock"] = active_clock
    app.extensions["recovery_limiter"] = attempt_limiter
    app.extensions["consent_repository"] = SqlConsentRepository(sessions)
    app.extensions["user_repository"] = user_repository
    app.extensions["verification_repository"] = SqlVerificationRepository(
        sessions, active_config.simulated_identity_outcomes
    )
    app.extensions["jwt_issuer"] = jwt_issuer
    app.extensions["token_service"] = TokenService(
        users=user_repository,
        refresh_tokens=SqlRefreshTokenRepository(sessions, active_clock),
        access_tokens=jwt_issuer,
        attempt_limiter=attempt_limiter,
        clock=active_clock,
        access_ttl=active_config.access_token_ttl,
        refresh_ttl=active_config.refresh_ttl,
        password_iterations=active_config.password_iterations,
    )
    app.extensions["registration_service"] = RegistrationService(
        users=user_repository,
        attempt_limiter=attempt_limiter,
        clock=active_clock,
        password_hasher=partial(hash_password, iterations=active_config.password_iterations),
    )
    configured_mail_sender = (
        SesRecoveryMailSender(
            active_config.recovery_ses_region,
            active_config.recovery_email_sender,
            active_config.recovery_web_url,
            delivery_mode=active_config.recovery_delivery_mode,
            admin_mailbox=active_config.recovery_admin_mailbox,
        )
        if active_config.recovery_ses_region
        else None
    )
    app.extensions["notification_mail_sender"] = configured_mail_sender
    app.extensions["password_recovery_service"] = PasswordRecoveryService(
        user_repository,
        SqlPasswordRecoveryRepository(sessions),
        recovery_mail_sender or (configured_mail_sender),
        attempt_limiter,
        active_clock,
        partial(hash_password, iterations=active_config.password_iterations),
    )
    if active_config.biometric_login_enabled:
        app.extensions["biometric_service"] = BiometricService(
            SqlBiometricRepository(sessions, active_clock),
            user_repository,
            app.extensions["token_service"],
            P256BiometricCrypto(
                active_config.jwt_private_key, active_config.jwt_issuer, active_config.jwt_audience
            ),
            RedisBiometricLimiter(
                redis_client,
                active_config.max_attempts,
                int(active_config.attempt_window.total_seconds()),
            ),
            active_clock,
            active_config.jwt_issuer,
        )

    app.register_blueprint(health.bp)
    app.register_blueprint(notification_mail.bp)
    app.register_blueprint(users.bp)
    app.register_blueprint(oauth.bp)
    app.register_blueprint(wellknown.bp)
    app.register_blueprint(consents.bp)
    app.register_blueprint(advisors.bp)
    app.register_blueprint(biometrics.bp)
    app.register_blueprint(session_state.bp)
    app.register_blueprint(verification.bp)
    app.register_blueprint(password_recovery.bp)

    @app.cli.command("create-admin")
    @click.option("--name", required=True)
    @click.option("--email", required=True)
    @click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
    def create_admin(name: str, email: str, password: str) -> None:
        try:
            payload = RegisterRequest(
                nombre=name.strip(), correo=email.strip(), contrasena=password
            )
        except ValueError as error:
            raise click.ClickException("Nombre, correo o contrasena invalidos.") from error
        user = User(uuid4(), payload.nombre, str(payload.correo).casefold(), "administrador")
        try:
            user_repository.add(
                user, hash_password(password, iterations=active_config.password_iterations)
            )
        except EmailAlreadyRegisteredError as error:
            raise click.ClickException(
                "La cuenta ya existe; no se modificaron credenciales ni permisos."
            ) from error
        click.echo(str(user.user_id))

    @app.cli.command("create-advisor")
    @click.option("--name", required=True)
    @click.option("--email", required=True)
    @click.option("--password", required=True, hide_input=True)
    @click.option("--partner-id", required=True)
    @click.option("--partner-name", required=True)
    def create_advisor(
        name: str,
        email: str,
        password: str,
        partner_id: str,
        partner_name: str,
    ) -> None:
        user = User(uuid4(), name.strip(), email.strip().casefold(), "asesor", (partner_id,))
        user_repository.create_advisor(
            user,
            hash_password(password, iterations=active_config.password_iterations),
            partner_id.strip(),
            partner_name.strip(),
        )
        click.echo(str(user.user_id))

    @app.cli.command("create-partner-user")
    @click.option("--name", required=True)
    @click.option("--email", required=True)
    @click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
    @click.option("--partner-id", required=True)
    @click.option("--partner-name", required=True)
    def create_partner_user(
        name: str, email: str, password: str, partner_id: str, partner_name: str
    ) -> None:
        payload = RegisterRequest(nombre=name.strip(), correo=email.strip(), contrasena=password)
        if not partner_name.strip() or len(partner_name.strip()) > 120:
            raise click.BadParameter("Nombre del socio invalido.", param_hint="partner-name")
        user = User(
            uuid4(), payload.nombre, str(payload.correo).casefold(), "socio", (partner_id.strip(),)
        )
        user_repository.create_partner_user(
            user,
            hash_password(password, iterations=active_config.password_iterations),
            partner_id.strip(),
            partner_name.strip(),
        )
        click.echo(str(user.user_id))

    @app.before_request
    def assign_correlation_id() -> None:
        g.correlation_id = _correlation_id(request.headers.get("X-Correlation-ID"))

    @app.after_request
    def include_correlation_id(response: Response) -> Response:
        response.headers["X-Correlation-ID"] = str(g.correlation_id)
        if request.path.startswith(
            (
                "/oauth/",
                "/v1/biometria/",
                "/v1/verificaciones-identidad",
                "/v1/recuperacion-contrasena",
            )
        ):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
        return response

    @app.errorhandler(BadRequest)
    @app.errorhandler(UnsupportedMediaType)
    def bad_request(_: BadRequest | UnsupportedMediaType) -> Response:
        return problem_response(
            status=400,
            code="solicitud_invalida",
            title="Solicitud inválida",
            detail="El cuerpo de la solicitud no es válido.",
        )

    @app.errorhandler(RedisError)
    @app.errorhandler(SQLAlchemyError)
    @app.errorhandler(TokenIssuanceError)
    def dependency_unavailable(_: Exception) -> Response:
        app.logger.error(
            "Identidad: dependencia no disponible", extra={"correlationId": str(g.correlation_id)}
        )
        return problem_response(
            status=503,
            code="servicio_no_disponible",
            title="Servicio no disponible",
            detail="Intenta nuevamente en unos minutos.",
        )

    return app


def _correlation_id(candidate: str | None) -> UUID:
    if candidate:
        try:
            return UUID(candidate)
        except ValueError:
            pass
    return uuid4()
