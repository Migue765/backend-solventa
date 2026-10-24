import pytest
from bff_web.config import Config


def test_defaults() -> None:
    config = Config(identity_url="http://identidad", jwt_issuer="https://iss")

    assert config.jwt_audience == "solventa-api"
    assert config.identity_timeout_seconds == 5
    assert config.jwks_endpoint == "http://identidad/.well-known/jwks.json"
    assert config.encrypted_login_enabled is False


def test_explicit_jwks_url_wins() -> None:
    config = Config(identity_url="http://identidad", jwt_issuer="x", jwks_url="https://j/keys")

    assert config.jwks_endpoint == "https://j/keys"


def test_environment_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IDENTIDAD_URL", "http://identidad.svc:8080/")
    monkeypatch.setenv("JWT_ISSUER", "https://api.solventa.co")
    monkeypatch.setenv("JWT_AUDIENCE", "otra")
    monkeypatch.setenv("JWKS_URL", "https://api.solventa.co/.well-known/jwks.json")
    monkeypatch.setenv("IDENTIDAD_TIMEOUT_SECONDS", "2.5")
    monkeypatch.setenv("QUOTE_SERVICE_URL", "http://cotizacion:8080/")

    config = Config.from_environment()

    assert config.identity_url == "http://identidad.svc:8080"
    assert config.jwt_audience == "otra"
    assert config.jwks_endpoint == "https://api.solventa.co/.well-known/jwks.json"
    assert config.identity_timeout_seconds == 2.5
    assert config.quote_service_url == "http://cotizacion:8080"


@pytest.mark.parametrize("missing", ["IDENTIDAD_URL", "JWT_ISSUER"])
def test_required_variables(monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    monkeypatch.setenv("IDENTIDAD_URL", "http://identidad")
    monkeypatch.setenv("JWT_ISSUER", "https://iss")
    monkeypatch.delenv(missing)

    with pytest.raises(ValueError, match=missing):
        Config.from_environment()


@pytest.mark.parametrize("value", ["0", "-1"])
def test_timeout_must_be_positive(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("IDENTIDAD_URL", "http://identidad")
    monkeypatch.setenv("JWT_ISSUER", "https://iss")
    monkeypatch.setenv("IDENTIDAD_TIMEOUT_SECONDS", value)

    with pytest.raises(ValueError, match="IDENTIDAD_TIMEOUT_SECONDS"):
        Config.from_environment()


def test_session_and_storage_settings_no_longer_exist(monkeypatch: pytest.MonkeyPatch) -> None:
    config = Config(identity_url="http://identidad", jwt_issuer="x")

    for removed in ("database_url", "redis_url", "session_cookie_name", "cors_allowed_origins"):
        assert not hasattr(config, removed)


def test_encrypted_login_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IDENTIDAD_URL", "http://identidad")
    monkeypatch.setenv("JWT_ISSUER", "https://iss")
    monkeypatch.setenv("ENCRYPTED_LOGIN_ENABLED", "true")
    assert Config.from_environment().encrypted_login_enabled


@pytest.mark.parametrize("value", ["yes", "1", "enabled", ""])
def test_invalid_encrypted_login_flag(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("IDENTIDAD_URL", "http://identidad")
    monkeypatch.setenv("JWT_ISSUER", "https://iss")
    monkeypatch.setenv("ENCRYPTED_LOGIN_ENABLED", value)
    with pytest.raises(ValueError, match="ENCRYPTED_LOGIN_ENABLED"):
        Config.from_environment()
