from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from asistencias.models import Base
from sqlalchemy import create_engine, inspect

SERVICE = Path(__file__).resolve().parents[1]


def test_migrations_read_database_url_from_environment_like_the_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # El Job solo recibe ASISTENCIAS_DATABASE_URL; una contrasena codificada trae "%".
    url = f"sqlite:///{tmp_path / 'asistencias%40job.db'}"
    monkeypatch.setenv("ASISTENCIAS_DATABASE_URL", url)
    command.upgrade(AlembicConfig(str(SERVICE / "alembic.ini")), "head")
    engine = create_engine(url)
    try:
        assert set(Base.metadata.tables) <= set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
