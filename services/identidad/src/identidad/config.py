import os
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal, cast
from urllib.parse import urlsplit

from pydantic import EmailStr, TypeAdapter

from identidad.domain.verification import MOCK_OUTCOMES
from identidad.infrastructure.passwords import DEFAULT_ITERATIONS

CLIENT_IDS = ("web", "movil")
SCHEMA_PATTERN = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
RecoveryDeliveryMode = Literal["usuario", "academico_administrado"]


def validate_recovery_delivery(mode: str, admin_mailbox: str) -> RecoveryDeliveryMode:
    if mode not in {"usuario", "academico_administrado"}:
        raise ValueError("RECOVERY_DELIVERY_MODE no es valido.")
    if mode == "academico_administrado":
        if not admin_mailbox.strip():
            raise ValueError("El modo academico requiere RECOVERY_ADMIN_MAILBOX.")
        TypeAdapter(EmailStr).validate_python(admin_mailbox)
    elif admin_mailbox:
        raise ValueError("RECOVERY_ADMIN_MAILBOX solo aplica al modo academico administrado.")
    return cast(RecoveryDeliveryMode, mode)


@dataclass(frozen=True)
class Config:
    database_url: str
    jwt_private_key: str
    jwt_issuer: str
    jwt_audience: str = "solventa-api"
    redis_url: str | None = None
    db_schema: str | None = None
    access_token_ttl: timedelta = timedelta(minutes=60)
    refresh_ttl_web: timedelta = timedelta(hours=8)
    refresh_ttl_movil: timedelta = timedelta(days=30)
    max_attempts: int = 5
    attempt_window: timedelta = timedelta(minutes=5)
    db_pool_size: int = 5
    db_max_overflow: int = 5
    password_iterations: int = DEFAULT_ITERATIONS
    internal_api_token: str = ""
    encrypted_login_enabled: bool = False
    biometric_login_enabled: bool = False
    login_encryption_private_key: str = ""
    simulated_identity_enabled: bool = False
    simulated_identity_outcomes: tuple[str, ...] = ("aprobado",)
    recovery_ses_region: str = ""
    recovery_email_sender: str = ""
    recovery_web_url: str = ""
    recovery_delivery_mode: RecoveryDeliveryMode = "usuario"
    recovery_admin_mailbox: str = ""

    def __post_init__(self) -> None:
        validate_recovery_delivery(self.recovery_delivery_mode, self.recovery_admin_mailbox)
        recovery_values = (
            self.recovery_ses_region,
            self.recovery_email_sender,
            self.recovery_web_url,
        )
        if any(recovery_values) or self.recovery_delivery_mode == "academico_administrado":
            if not all(value.strip() for value in recovery_values):
                raise ValueError("Recuperacion requiere region SES, remitente y URL Web.")
            parsed = urlsplit(self.recovery_web_url)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("RECOVERY_WEB_URL requiere base HTTPS sin query ni fragmento.")
            if not self.encrypted_login_enabled:
                raise ValueError("Recuperacion requiere ENCRYPTED_LOGIN_ENABLED.")
            TypeAdapter(EmailStr).validate_python(self.recovery_email_sender)
        if not 1 <= len(self.simulated_identity_outcomes) <= 3 or any(
            value not in MOCK_OUTCOMES for value in self.simulated_identity_outcomes
        ):
            raise ValueError("IDENTIDAD_SIMULADA_RESULTADOS requiere de 1 a 3 resultados validos.")
        if not self.database_url.strip():
            raise ValueError("DATABASE_URL no puede estar vacía.")
        if not self.jwt_private_key.strip():
            raise ValueError("JWT_PRIVATE_KEY no puede estar vacía.")
        if not self.jwt_issuer.strip() or self.jwt_issuer.endswith("/"):
            raise ValueError("JWT_ISSUER es obligatorio y no puede terminar en '/'.")
        if self.db_schema is not None and not SCHEMA_PATTERN.fullmatch(self.db_schema):
            raise ValueError("DB_SCHEMA solo admite minúsculas, dígitos y guion bajo.")
        if self.encrypted_login_enabled and (
            not self.redis_url or not self.login_encryption_private_key.strip()
        ):
            raise ValueError("Encrypted login requiere REDIS_URL y LOGIN_ENCRYPTION_PRIVATE_KEY.")
        if self.biometric_login_enabled and not self.redis_url:
            raise ValueError("Biometric login requiere REDIS_URL para limites compartidos.")

    def refresh_ttl(self, client_id: str) -> timedelta:
        if client_id == "web":
            return self.refresh_ttl_web
        if client_id == "movil":
            return self.refresh_ttl_movil
        raise ValueError(f"client_id desconocido: {client_id}")

    @classmethod
    def from_environment(cls) -> "Config":
        admin_mailbox = os.getenv("RECOVERY_ADMIN_MAILBOX", "").strip()
        return cls(
            recovery_delivery_mode=validate_recovery_delivery(
                os.getenv("RECOVERY_DELIVERY_MODE", "usuario").strip(), admin_mailbox
            ),
            recovery_admin_mailbox=admin_mailbox,
            recovery_ses_region=os.getenv("RECOVERY_SES_REGION", "").strip(),
            recovery_email_sender=os.getenv("RECOVERY_EMAIL_SENDER", "").strip(),
            recovery_web_url=os.getenv("RECOVERY_WEB_URL", "").strip(),
            simulated_identity_enabled=_boolean("IDENTIDAD_SIMULADA_ENABLED"),
            simulated_identity_outcomes=tuple(
                item.strip()
                for item in os.getenv("IDENTIDAD_SIMULADA_RESULTADOS", "aprobado").split(",")
            ),
            biometric_login_enabled=_boolean("BIOMETRIC_LOGIN_ENABLED"),
            encrypted_login_enabled=_boolean("ENCRYPTED_LOGIN_ENABLED"),
            login_encryption_private_key=normalize_pem(
                os.getenv("LOGIN_ENCRYPTION_PRIVATE_KEY", "")
            ),
            internal_api_token=os.getenv("INTERNAL_API_TOKEN", "").strip(),
            database_url=_required("DATABASE_URL"),
            jwt_private_key=normalize_pem(_required("JWT_PRIVATE_KEY")),
            jwt_issuer=_required("JWT_ISSUER").rstrip("/"),
            jwt_audience=os.getenv("JWT_AUDIENCE") or "solventa-api",
            redis_url=os.getenv("REDIS_URL") or None,
            db_schema=os.getenv("DB_SCHEMA") or None,
            access_token_ttl=timedelta(minutes=_positive_int("ACCESS_TOKEN_MINUTES", 60)),
            refresh_ttl_web=timedelta(hours=_positive_int("REFRESH_TOKEN_HOURS_WEB", 8)),
            refresh_ttl_movil=timedelta(days=_positive_int("REFRESH_TOKEN_DAYS_MOVIL", 30)),
            max_attempts=_positive_int("AUTH_MAX_ATTEMPTS", 5),
            attempt_window=timedelta(seconds=_positive_int("AUTH_ATTEMPT_WINDOW_SECONDS", 300)),
            db_pool_size=_positive_int("DB_POOL_SIZE", 5),
            db_max_overflow=_positive_int("DB_MAX_OVERFLOW", 5),
        )


def normalize_pem(value: str) -> str:
    return value.replace("\\n", "\n").strip()


def _boolean(name: str) -> bool:
    value = os.getenv(name, "false").strip().lower()
    if value not in {"true", "false"}:
        raise ValueError(f"{name} solo admite true o false.")
    return value == "true"


def _required(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise ValueError(f"{name} es obligatoria.")
    return value


def _positive_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value < 1:
        raise ValueError(f"{name} debe ser mayor que cero.")
    return value
