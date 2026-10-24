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

from identidad.api import advisors, consents, health, oauth, users, wellknown
from identidad.api.schemas import RegisterRequest
from identidad.application.ports import LoginAttemptLimiter
from identidad.application.registration_service import RegistrationService
from identidad.application.token_service import TokenService
from identidad.config import Config
from identidad.domain.auth import User
from identidad.infrastructure.encrypted_login import EncryptedLogin
from identidad.infrastructure.jwt_issuer import RsaJwtIssuer
from identidad.infrastructure.memory_attempt_limiter import InMemoryLoginAttemptLimiter
from identidad.infrastructure.passwords import hash_password
from identidad.infrastructure.persistence.consent_repository import SqlConsentRepository
from identidad.infrastructure.persistence.database import create_sessions
from identidad.infrastructure.persistence.refresh_token_repository import (
    SqlRefreshTokenRepository,
)
from identidad.infrastructure.persistence.user_repository import SqlUserRepository
from identidad.infrastructure.redis_attempt_limiter import RedisLoginAttemptLimiter
from identidad.problems import problem_response


def create_app(
    config: Config | None = None,
    clock: Callable[[], datetime] | None = None,
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
    app.extensions["consent_repository"] = SqlConsentRepository(sessions)
    app.extensions["user_repository"] = user_repository
    app.extensions["jwt_issuer"] = jwt_issuer
    app.extensions["token_service"] = TokenService(
        users=user_repository,
        refresh_tokens=SqlRefreshTokenRepository(sessions),
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

    app.register_blueprint(health.bp)
    app.register_blueprint(users.bp)
    app.register_blueprint(oauth.bp)
    app.register_blueprint(wellknown.bp)
    app.register_blueprint(consents.bp)
    app.register_blueprint(advisors.bp)

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
        if request.path.startswith("/oauth/"):
            response.headers["Cache-Control"] = "no-store"
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
    def dependency_unavailable(_: Exception) -> Response:
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
