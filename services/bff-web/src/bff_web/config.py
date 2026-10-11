import json
import os
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID

from pydantic import EmailStr, TypeAdapter

from bff_web.domain.auth import User
from bff_web.infrastructure.development_credentials import DevelopmentCredential

EMAIL_ADAPTER: TypeAdapter[EmailStr] = TypeAdapter(EmailStr)


@dataclass(frozen=True)
class Config:
    credentials: tuple[DevelopmentCredential, ...] = ()
    session_duration: timedelta = timedelta(minutes=30)
    session_cookie_name: str = "solventa_session"
    session_cookie_secure: bool = True
    session_cookie_samesite: str = "Lax"
    max_attempts: int = 5
    attempt_window: timedelta = timedelta(minutes=5)
    cors_allowed_origins: tuple[str, ...] = ("http://localhost:4200",)

    def __post_init__(self) -> None:
        if not self.session_cookie_name.strip():
            raise ValueError("SESSION_COOKIE_NAME no puede estar vacío.")
        if self.session_cookie_samesite == "None" and not self.session_cookie_secure:
            raise ValueError("SESSION_COOKIE_SAMESITE=None requiere SESSION_COOKIE_SECURE=true.")
        if not self.cors_allowed_origins:
            raise ValueError("CORS_ALLOWED_ORIGINS debe contener al menos un origen.")

    @classmethod
    def from_environment(cls) -> "Config":
        return cls(
            credentials=_parse_credentials(os.getenv("AUTH_DEVELOPMENT_USERS_JSON", "[]")),
            session_duration=timedelta(minutes=_positive_int("SESSION_DURATION_MINUTES", 30)),
            session_cookie_name=os.getenv("SESSION_COOKIE_NAME", "solventa_session"),
            session_cookie_secure=_boolean("SESSION_COOKIE_SECURE", True),
            session_cookie_samesite=_same_site(os.getenv("SESSION_COOKIE_SAMESITE", "Lax")),
            max_attempts=_positive_int("AUTH_MAX_ATTEMPTS", 5),
            attempt_window=timedelta(seconds=_positive_int("AUTH_ATTEMPT_WINDOW_SECONDS", 300)),
            cors_allowed_origins=tuple(
                origin.strip()
                for origin in os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost:4200").split(",")
                if origin.strip()
            ),
        )


def _parse_credentials(raw_credentials: str) -> tuple[DevelopmentCredential, ...]:
    try:
        items: Any = json.loads(raw_credentials)
        if not isinstance(items, list):
            raise ValueError("debe ser una lista")
        credentials = tuple(_parse_credential(item) for item in items)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("AUTH_DEVELOPMENT_USERS_JSON no es válido.") from error

    emails = [credential.user.email.casefold() for credential in credentials]
    if len(emails) != len(set(emails)):
        raise ValueError("AUTH_DEVELOPMENT_USERS_JSON contiene correos duplicados.")
    return credentials


def _parse_credential(item: Any) -> DevelopmentCredential:
    if not isinstance(item, dict):
        raise ValueError("cada credencial debe ser un objeto")
    role = _required_text(item["rol"], "rol")
    if role != "cliente":
        raise ValueError("el rol de desarrollo debe ser cliente")

    return DevelopmentCredential(
        user=User(
            user_id=UUID(item["id"]),
            name=_required_text(item["nombre"], "nombre"),
            email=str(EMAIL_ADAPTER.validate_python(item["correo"])).casefold(),
            role=role,
        ),
        password_hash=_required_text(item["passwordHash"], "passwordHash"),
    )


def _positive_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value < 1:
        raise ValueError(f"{name} debe ser mayor que cero.")
    return value


def _boolean(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().casefold()
    if normalized not in {"true", "false"}:
        raise ValueError(f"{name} debe ser true o false.")
    return normalized == "true"


def _same_site(value: str) -> str:
    normalized = value.strip().capitalize()
    if normalized not in {"Lax", "Strict", "None"}:
        raise ValueError("SESSION_COOKIE_SAMESITE debe ser Lax, Strict o None.")
    return normalized


def _required_text(value: Any, field: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{field} no puede estar vacío")
    return normalized
