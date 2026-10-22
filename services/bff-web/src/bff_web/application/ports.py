from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from bff_web.domain.auth import AccessClaims, TokenSet, User

Clock = Callable[[], datetime]


class IdentityGateway(Protocol):
    def register(self, name: str, email: str, password: str, correlation_id: str) -> User: ...

    def login(self, email: str, password: str, correlation_id: str) -> TokenSet: ...

    def refresh(self, refresh_token: str, correlation_id: str) -> TokenSet: ...

    def revoke(self, refresh_token: str, correlation_id: str) -> None: ...

    def list_clients(self, correlation_id: str) -> tuple[User, ...]: ...


class TokenVerifier(Protocol):
    def verify(self, token: str) -> AccessClaims: ...
