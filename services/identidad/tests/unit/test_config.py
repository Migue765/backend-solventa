from datetime import timedelta

import pytest
from identidad.config import Config


def set_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("JWT_PRIVATE_KEY", "-----BEGIN-----\\nabc\\n-----END-----")
    monkeypatch.setenv("JWT_ISSUER", "https://api.solventa.test/")


def test_defaults_match_the_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    set_required(monkeypatch)

    config = Config.from_environment()

    assert config.access_token_ttl == timedelta(hours=1)
    assert config.refresh_ttl("web") == timedelta(hours=8)
    assert config.refresh_ttl("movil") == timedelta(days=30)
    assert config.jwt_audience == "solventa-api"
    assert config.max_attempts == 5
    assert config.attempt_window == timedelta(seconds=300)
    assert config.redis_url is None
    assert config.db_schema is None


def test_issuer_trailing_slash_and_escaped_newlines_are_normalized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_required(monkeypatch)

    config = Config.from_environment()

    assert config.jwt_issuer == "https://api.solventa.test"
    assert config.jwt_private_key.splitlines() == ["-----BEGIN-----", "abc", "-----END-----"]


@pytest.mark.parametrize("missing", ["DATABASE_URL", "JWT_PRIVATE_KEY", "JWT_ISSUER"])
def test_required_variables_fail_fast(monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    set_required(monkeypatch)
    monkeypatch.delenv(missing)

    with pytest.raises(ValueError, match=missing):
        Config.from_environment()


def test_environment_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    set_required(monkeypatch)
    monkeypatch.setenv("ACCESS_TOKEN_MINUTES", "5")
    monkeypatch.setenv("REFRESH_TOKEN_HOURS_WEB", "2")
    monkeypatch.setenv("REFRESH_TOKEN_DAYS_MOVIL", "7")
    monkeypatch.setenv("DB_SCHEMA", "identidad")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

    config = Config.from_environment()

    assert config.access_token_ttl == timedelta(minutes=5)
    assert config.refresh_ttl("web") == timedelta(hours=2)
    assert config.refresh_ttl("movil") == timedelta(days=7)
    assert config.db_schema == "identidad"
    assert config.redis_url == "redis://localhost:6379/0"


@pytest.mark.parametrize("name", ["ACCESS_TOKEN_MINUTES", "AUTH_MAX_ATTEMPTS"])
def test_non_positive_numbers_are_rejected(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    set_required(monkeypatch)
    monkeypatch.setenv(name, "0")

    with pytest.raises(ValueError, match=name):
        Config.from_environment()


@pytest.mark.parametrize("schema", ["Identidad", "a;drop", "1abc", "con espacio", '"x"'])
def test_invalid_schema_names_are_rejected(schema: str) -> None:
    with pytest.raises(ValueError, match="DB_SCHEMA"):
        Config(
            database_url="sqlite://", jwt_private_key="k", jwt_issuer="https://x", db_schema=schema
        )


def test_issuer_with_trailing_slash_is_rejected_when_built_directly() -> None:
    with pytest.raises(ValueError, match="JWT_ISSUER"):
        Config(database_url="sqlite://", jwt_private_key="k", jwt_issuer="https://x/")


def test_unknown_client_has_no_refresh_ttl() -> None:
    config = Config(database_url="sqlite://", jwt_private_key="k", jwt_issuer="https://x")

    with pytest.raises(ValueError):
        config.refresh_ttl("otro")
