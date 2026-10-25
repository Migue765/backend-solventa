from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from uuid import UUID


@dataclass(frozen=True)
class User:
    user_id: UUID
    name: str
    email: str
    role: str
    partner_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class StoredCredential:
    user: User
    password_hash: str


@dataclass(frozen=True)
class NewRefreshToken:
    token_hash: str
    user_id: UUID
    family_id: UUID
    client_id: str
    created_at: datetime
    expires_at: datetime
    biometric_credential_id: UUID | None = None


class RotationOutcome(Enum):
    ROTATED = "rotated"
    INVALID = "invalid"
    REUSED = "reused"


@dataclass(frozen=True)
class RotationResult:
    outcome: RotationOutcome
    user_id: UUID | None = None


@dataclass(frozen=True)
class IssuedTokens:
    access_token: str
    expires_in: int
    refresh_token: str
    refresh_expires_in: int
    user: User
