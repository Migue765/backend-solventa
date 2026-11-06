from collections.abc import Iterator
from pathlib import Path

import pytest
from flask import Flask
from flask.testing import FlaskClient
from notificaciones.app import create_app
from support import FakeDeliveryGateway, MutableClock


@pytest.fixture
def clock() -> MutableClock:
    return MutableClock()


@pytest.fixture
def gateway() -> FakeDeliveryGateway:
    return FakeDeliveryGateway()


@pytest.fixture
def app(tmp_path: Path, clock: MutableClock, gateway: FakeDeliveryGateway) -> Iterator[Flask]:
    flask_app = create_app(
        f"sqlite:///{tmp_path / 'notificaciones.db'}",
        "token-interno",
        gateway,
        clock,
        create_schema=True,
    )
    yield flask_app
    flask_app.extensions["engine"].dispose()


@pytest.fixture
def client(app: Flask) -> FlaskClient:
    return app.test_client()


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer token-interno"}
