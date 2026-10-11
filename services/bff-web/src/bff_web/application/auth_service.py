from datetime import timedelta

from bff_web.application.ports import (
    Clock,
    CredentialProvider,
    LoginAttemptLimiter,
    SessionRepository,
)
from bff_web.domain.auth import StartedSession, UserSession
from bff_web.domain.errors import InvalidCredentialsError, UnauthorizedError


class AuthService:
    def __init__(
        self,
        credentials: CredentialProvider,
        sessions: SessionRepository,
        attempt_limiter: LoginAttemptLimiter,
        clock: Clock,
        session_duration: timedelta,
    ) -> None:
        self._credentials = credentials
        self._sessions = sessions
        self._attempt_limiter = attempt_limiter
        self._clock = clock
        self._session_duration = session_duration

    def login(self, email: str, password: str, attempt_key: str) -> StartedSession:
        now = self._clock()
        normalized_email = email.strip().casefold()
        self._attempt_limiter.ensure_allowed(attempt_key, now)

        try:
            user = self._credentials.authenticate(normalized_email, password)
        except InvalidCredentialsError:
            self._attempt_limiter.register_failure(attempt_key, now)
            raise

        self._attempt_limiter.reset(attempt_key)
        session = UserSession(user=user, expires_at=now + self._session_duration)
        return StartedSession(token=self._sessions.create(session), session=session)

    def current_session(self, token: str | None) -> UserSession:
        if not token:
            raise UnauthorizedError()

        session = self._sessions.get(token, self._clock())
        if session is None:
            raise UnauthorizedError()

        return session

    def logout(self, token: str | None) -> None:
        if token:
            self._sessions.delete(token)
