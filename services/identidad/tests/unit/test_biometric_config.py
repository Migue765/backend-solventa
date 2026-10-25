import pytest
from identidad.config import Config


def test_biometric_flag_cannot_fall_back_to_memory() -> None:
    with pytest.raises(ValueError, match="REDIS_URL"):
        Config(
            database_url="sqlite://",
            jwt_private_key="unused",
            jwt_issuer="https://identidad.solventa.test",
            biometric_login_enabled=True,
        )


def test_invalid_flag_is_not_silently_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BIOMETRIC_LOGIN_ENABLED", "yes")
    with pytest.raises(ValueError, match="BIOMETRIC_LOGIN_ENABLED"):
        Config.from_environment()
