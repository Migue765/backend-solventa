from collections.abc import Callable
from datetime import datetime
from typing import Protocol
from uuid import UUID

from identidad.domain.auth import NewRefreshToken, RotationResult, StoredCredential, User

Clock = Callable[[], datetime]


class UserRepository(Protocol):
    def add(self, user: User, password_hash: str) -> None: ...

    def find_credential(self, email: str) -> StoredCredential | None: ...

    def find_by_id(self, user_id: UUID) -> User | None: ...

    def find_client_by_document(self, document_type: str, identification: str) -> User | None: ...


class RefreshTokenRepository(Protocol):
    def add(self, token: NewRefreshToken) -> None: ...

    def rotate(
        self,
        token_hash: str,
        client_id: str,
        new_token_hash: str,
        now: datetime,
        expires_at: datetime,
    ) -> RotationResult: ...

    def revoke_family(self, token_hash: str, now: datetime) -> None: ...


class AccessTokenIssuer(Protocol):
    def issue(
        self, user: User, client_id: str, issued_at: datetime, expires_at: datetime
    ) -> str: ...


class LoginAttemptLimiter(Protocol):
    def ensure_allowed(self, key: str, now: datetime) -> None: ...

    def register_failure(self, key: str, now: datetime) -> None: ...

    def reset(self, key: str) -> None: ...
