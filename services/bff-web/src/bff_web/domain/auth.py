from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

CHANNEL = "web"


@dataclass(frozen=True)
class User:
    user_id: UUID
    name: str
    email: str
    role: str
    document_type: str = "cc"
    identification: str = "1234567890"
    phone: str = "+573001234567"
    partner_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class TokenSet:
    access_token: str
    refresh_token: str
    expires_in: int
    refresh_expires_in: int
    user: User


@dataclass(frozen=True)
class AccessClaims:
    user: User
    client_id: str
    scope: str
    expires_at: datetime
