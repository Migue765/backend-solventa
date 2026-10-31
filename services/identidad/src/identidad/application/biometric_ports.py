from collections.abc import Callable
from datetime import datetime
from typing import Protocol
from uuid import UUID

from identidad.domain.auth import IssuedTokens, NewRefreshToken
from identidad.domain.biometric import BiometricChallenge, BiometricCredential


class BiometricRepository(Protocol):
    def add_challenge(self, challenge: BiometricChallenge) -> None: ...

    def challenge(self, challenge_id: str) -> BiometricChallenge | None: ...

    def credential(self, credential_id: UUID) -> BiometricCredential | None: ...

    def enroll(self, challenge: BiometricChallenge, correlation: str) -> bool: ...

    def login(
        self,
        challenge: BiometricChallenge,
        issue: Callable[[], tuple[NewRefreshToken, IssuedTokens]],
        correlation: str,
    ) -> IssuedTokens | None: ...

    def credentials(self, user_id: UUID) -> tuple[BiometricCredential, ...]: ...

    def revoke(self, user_id: UUID, credential_id: UUID, correlation: str) -> None: ...

    def audit(
        self,
        operation: str,
        result: str,
        now: datetime,
        correlation: str,
        user_id: UUID | None = None,
        credential_id: UUID | None = None,
    ) -> None: ...


class BiometricCrypto(Protocol):
    def public_key(self, encoded: str) -> bytes: ...

    def verify(self, public_key: bytes, message: bytes, signature: str) -> None: ...

    def subject(self, bearer: str) -> UUID: ...


class BiometricLimiter(Protocol):
    def reserve(self, key: str) -> None: ...
