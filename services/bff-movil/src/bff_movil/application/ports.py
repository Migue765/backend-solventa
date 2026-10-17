from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from bff_movil.domain.auth import AccessClaims, TokenSet, User

Clock = Callable[[], datetime]


class IdentityGateway(Protocol):
    def register(self, name: str, email: str, password: str, correlation_id: str) -> User: ...

    def login(self, email: str, password: str, correlation_id: str) -> TokenSet: ...

    def refresh(self, refresh_token: str, correlation_id: str) -> TokenSet: ...

    def revoke(self, refresh_token: str, correlation_id: str) -> None: ...


class TokenVerifier(Protocol):
    def verify(self, token: str) -> AccessClaims: ...
