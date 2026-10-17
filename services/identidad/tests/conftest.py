from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from flask import Flask
from flask.testing import FlaskClient
from identidad.app import create_app
from identidad.config import Config
from identidad.infrastructure.persistence.models import Base
from sqlalchemy import create_engine

ISSUER = "https://api.solventa.test"


class MutableClock:
    def __init__(self) -> None:
        self.now = datetime.now(UTC).replace(microsecond=0)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


@pytest.fixture(scope="session")
def private_key_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")


@pytest.fixture
def clock() -> MutableClock:
    return MutableClock()


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    url = f"sqlite:///{tmp_path / 'identidad.db'}"
    Base.metadata.create_all(create_engine(url))
    return url


@pytest.fixture
def make_app(private_key_pem: str, database_url: str, clock: MutableClock) -> Callable[..., Flask]:
    def factory(**overrides: Any) -> Flask:
        values: dict[str, Any] = {
            "database_url": database_url,
            "jwt_private_key": private_key_pem,
            "jwt_issuer": ISSUER,
            "password_iterations": 1000,
            "max_attempts": 3,
        }
        values.update(overrides)
        return create_app(Config(**values), clock=clock)

    return factory


@pytest.fixture
def app(make_app: Callable[..., Flask]) -> Flask:
    return make_app()


@pytest.fixture
def client(app: Flask) -> FlaskClient:
    return app.test_client()
