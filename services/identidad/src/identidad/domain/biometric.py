from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


class BiometricError(Exception):
    def __init__(self, code: str = "credenciales_invalidas", status: int = 401) -> None:
        self.code = code
        self.status = status
        super().__init__(code)


@dataclass(frozen=True)
class BiometricCredential:
    credential_id: UUID
    user_id: UUID
    public_key: bytes
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


@dataclass(frozen=True)
class BiometricChallenge:
    challenge_id: str
    credential_id: UUID
    user_id: UUID
    purpose: str
    public_key: bytes
    message: bytes
    created_at: datetime
    expires_at: datetime
    consumed: bool = False
