from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from identidad.domain.auth import NewRefreshToken, RotationOutcome, User
from identidad.infrastructure.persistence.database import create_sessions
from identidad.infrastructure.persistence.models import Base, RefreshTokenModel
from identidad.infrastructure.persistence.refresh_token_repository import (
    SqlRefreshTokenRepository,
)
from identidad.infrastructure.persistence.user_repository import SqlUserRepository
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
USER = User(uuid4(), "Ana", "ana@solventa.co", "cliente")


class Setup:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions
        self.repository = SqlRefreshTokenRepository(sessions)
        self.family = uuid4()

    def seed(self, token_hash: str, client_id: str = "web", expires_in: int = 3600) -> None:
        self.repository.add(
            NewRefreshToken(
                token_hash,
                USER.user_id,
                self.family,
                client_id,
                NOW,
                NOW + timedelta(seconds=expires_in),
            )
        )

    def revoked(self, token_hash: str) -> bool:
        with self.sessions() as session:
            row = session.scalar(
                select(RefreshTokenModel).where(RefreshTokenModel.token_hash == token_hash)
            )
        assert row is not None
        return row.revocado_en is not None


@pytest.fixture
def setup(tmp_path: Path) -> Iterator[Setup]:
    url = f"sqlite:///{tmp_path / 'tokens.db'}"
    Base.metadata.create_all(create_engine(url))
    sessions = create_sessions(url)
    SqlUserRepository(sessions).add(USER, "hash")
    yield Setup(sessions)


def test_rotation_revokes_the_old_token_and_keeps_the_family(setup: Setup) -> None:
    setup.seed("a")

    result = setup.repository.rotate("a", "web", "b", NOW, NOW + timedelta(hours=8))

    assert result.outcome is RotationOutcome.ROTATED
    assert result.user_id == USER.user_id
    assert setup.revoked("a") and not setup.revoked("b")
    with setup.sessions() as session:
        new = session.get(RefreshTokenModel, "b")
        old = session.get(RefreshTokenModel, "a")
    assert new is not None and old is not None
    assert new.family_id == old.family_id == setup.family
    assert old.reemplazado_por == "b"


def test_unknown_token_is_invalid(setup: Setup) -> None:
    result = setup.repository.rotate("x", "web", "y", NOW, NOW + timedelta(hours=8))

    assert result.outcome is RotationOutcome.INVALID


def test_other_client_is_invalid_and_does_not_revoke(setup: Setup) -> None:
    setup.seed("a", client_id="web")

    result = setup.repository.rotate("a", "movil", "b", NOW, NOW + timedelta(days=30))

    assert result.outcome is RotationOutcome.INVALID
    assert not setup.revoked("a")


def test_expired_token_is_invalid(setup: Setup) -> None:
    setup.seed("a", expires_in=60)

    result = setup.repository.rotate(
        "a", "web", "b", NOW + timedelta(seconds=61), NOW + timedelta(hours=8)
    )

    assert result.outcome is RotationOutcome.INVALID


def test_reuse_revokes_the_whole_family(setup: Setup) -> None:
    setup.seed("a")
    setup.repository.rotate("a", "web", "b", NOW, NOW + timedelta(hours=8))

    result = setup.repository.rotate("a", "web", "c", NOW, NOW + timedelta(hours=8))

    assert result.outcome is RotationOutcome.REUSED
    assert setup.revoked("b")
    with setup.sessions() as session:
        assert session.get(RefreshTokenModel, "c") is None


def test_revoke_family_is_idempotent_and_ignores_unknown(setup: Setup) -> None:
    setup.seed("a")
    setup.repository.rotate("a", "web", "b", NOW, NOW + timedelta(hours=8))

    setup.repository.revoke_family("a", NOW)
    setup.repository.revoke_family("a", NOW)
    setup.repository.revoke_family("desconocido", NOW)

    assert setup.revoked("a") and setup.revoked("b")
