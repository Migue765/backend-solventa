from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from bff_web.application.auth_service import AuthService
from bff_web.domain.auth import User
from bff_web.domain.errors import (
    InvalidCredentialsError,
    TooManyAttemptsError,
    UnauthorizedError,
)
from bff_web.infrastructure.development_credentials import (
    DevelopmentCredential,
    DevelopmentCredentialProvider,
)
from bff_web.infrastructure.memory_attempt_limiter import InMemoryLoginAttemptLimiter
from bff_web.infrastructure.memory_sessions import InMemorySessionRepository
from bff_web.infrastructure.passwords import hash_password

NOW = datetime(2026, 10, 11, 17, 0, tzinfo=UTC)
USER = User(uuid4(), "Cliente Solventa", "cliente@solventa.co", "cliente")


def build_service(max_attempts: int = 5) -> AuthService:
    return AuthService(
        credentials=DevelopmentCredentialProvider(
            (
                DevelopmentCredential(
                    USER,
                    hash_password("Solventa123!", salt=b"0123456789abcdef"),
                ),
            )
        ),
        sessions=InMemorySessionRepository(),
        attempt_limiter=InMemoryLoginAttemptLimiter(
            max_attempts=max_attempts,
            window=timedelta(minutes=5),
        ),
        clock=lambda: NOW,
        session_duration=timedelta(minutes=30),
    )


def test_login_creates_a_session_associated_with_the_user() -> None:
    service = build_service()

    started = service.login("CLIENTE@SOLVENTA.CO", "Solventa123!", "client:email")

    assert started.session.user == USER
    assert service.current_session(started.token) == started.session


def test_invalid_credentials_use_a_generic_error() -> None:
    service = build_service()

    with pytest.raises(InvalidCredentialsError):
        service.login("desconocido@solventa.co", "incorrecta", "client:email")


def test_attempts_are_temporarily_limited() -> None:
    service = build_service(max_attempts=1)

    with pytest.raises(InvalidCredentialsError):
        service.login("cliente@solventa.co", "incorrecta", "client:email")

    with pytest.raises(TooManyAttemptsError) as error:
        service.login("cliente@solventa.co", "incorrecta", "client:email")

    assert error.value.retry_after == 300


def test_logout_invalidates_the_session() -> None:
    service = build_service()
    started = service.login("cliente@solventa.co", "Solventa123!", "client:email")

    service.logout(started.token)

    with pytest.raises(UnauthorizedError):
        service.current_session(started.token)
