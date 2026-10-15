import json
from uuid import uuid4

import pytest
from bff_web.config import Config
from bff_web.infrastructure.passwords import hash_password


def test_configuration_loads_development_users(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "AUTH_DEVELOPMENT_USERS_JSON",
        json.dumps(
            [
                {
                    "id": str(uuid4()),
                    "nombre": "Cliente Solventa",
                    "correo": "CLIENTE@SOLVENTA.CO",
                    "rol": "cliente",
                    "tipoDocumento": "cc",
                    "identificacion": "1023456789",
                    "telefono": "+573001234567",
                    "passwordHash": hash_password(
                        "Solventa123!",
                        salt=b"0123456789abcdef",
                    ),
                }
            ]
        ),
    )

    config = Config.from_environment()

    assert config.credentials[0].user.email == "cliente@solventa.co"
    assert config.credentials[0].user.identification == "1023456789"


def test_insecure_cross_site_cookie_is_rejected() -> None:
    with pytest.raises(ValueError, match="requiere SESSION_COOKIE_SECURE"):
        Config(session_cookie_secure=False, session_cookie_samesite="None")
