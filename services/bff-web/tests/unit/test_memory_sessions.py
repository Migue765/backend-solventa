from datetime import UTC, datetime, timedelta
from uuid import uuid4

from bff_web.domain.auth import User, UserSession
from bff_web.infrastructure.memory_sessions import InMemorySessionRepository


def test_expired_sessions_are_removed() -> None:
    repository = InMemorySessionRepository()
    now = datetime(2026, 10, 11, 17, 0, tzinfo=UTC)
    session = UserSession(
        user=User(uuid4(), "Cliente", "cliente@solventa.co", "cliente"),
        expires_at=now + timedelta(minutes=1),
    )
    token = repository.create(session)

    assert repository.get(token, now) == session
    assert repository.get(token, now + timedelta(minutes=1)) is None
