from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from cotizacion.app import create_app
from cotizacion.config import Config

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


@pytest.fixture
def alembic_config(tmp_path):
    config = AlembicConfig(str(ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{tmp_path / 'cotizacion.db'}")
    return config


@pytest.fixture
def database_url(alembic_config):
    command.upgrade(alembic_config, "head")
    return alembic_config.get_main_option("sqlalchemy.url")


@pytest.fixture
def client(database_url):
    app = create_app(Config(database_url=database_url, tasa_base=Decimal("0.01"), vigencia_dias=15))
    return app.test_client()
