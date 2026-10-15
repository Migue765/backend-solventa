from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class User:
    user_id: UUID
    name: str
    email: str
    role: str
    document_type: str = "cc"
    identification: str = "1234567890"
    phone: str = "+573001234567"


@dataclass(frozen=True)
class UserSession:
    user: User
    expires_at: datetime


@dataclass(frozen=True)
class StartedSession:
    token: str
    session: UserSession
