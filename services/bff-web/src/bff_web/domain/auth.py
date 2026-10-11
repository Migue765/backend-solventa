from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class User:
    user_id: UUID
    name: str
    email: str
    role: str


@dataclass(frozen=True)
class UserSession:
    user: User
    expires_at: datetime


@dataclass(frozen=True)
class StartedSession:
    token: str
    session: UserSession
