from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig

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
