from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import create_engine, inspect

SERVICE_DIR = Path(__file__).resolve().parents[2]


def test_migrations_create_both_tables_with_their_own_version_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = f"sqlite:///{tmp_path / 'migrations.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("DB_SCHEMA", "identidad")
    config = AlembicConfig(str(SERVICE_DIR / "alembic.ini"))

    command.upgrade(config, "head")

    inspector = inspect(create_engine(url))
    assert {"usuarios", "refresh_tokens", "alembic_version_identidad"} <= set(
        inspector.get_table_names()
    )
    assert "alembic_version" not in inspector.get_table_names()
    columns = {column["name"] for column in inspector.get_columns("refresh_tokens")}
    assert columns == {
        "token_hash",
        "usuario_id",
        "family_id",
        "client_id",
        "creado_en",
        "expira_en",
        "revocado_en",
        "reemplazado_por",
    }

    command.downgrade(config, "base")
    remaining = set(inspect(create_engine(url)).get_table_names())
    assert "usuarios" not in remaining and "refresh_tokens" not in remaining
