from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from bff_web.domain.auth import User, UserSession

Clock = Callable[[], datetime]


class CredentialProvider(Protocol):
    def authenticate(self, email: str, password: str) -> User: ...


class SessionRepository(Protocol):
    def create(self, session: UserSession) -> str: ...

    def get(self, token: str, now: datetime) -> UserSession | None: ...

    def delete(self, token: str) -> None: ...


class LoginAttemptLimiter(Protocol):
    def ensure_allowed(self, key: str, now: datetime) -> None: ...

    def register_failure(self, key: str, now: datetime) -> None: ...

    def reset(self, key: str) -> None: ...
