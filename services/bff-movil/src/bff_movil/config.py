import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    identity_url: str
    jwt_issuer: str
    jwt_audience: str = "solventa-api"
    jwks_url: str | None = None
    identity_timeout_seconds: float = 5

    def __post_init__(self) -> None:
        if not self.identity_url.strip():
            raise ValueError("IDENTIDAD_URL no puede estar vacía.")
        if not self.jwt_issuer.strip():
            raise ValueError("JWT_ISSUER no puede estar vacío.")
        if self.identity_timeout_seconds <= 0:
            raise ValueError("IDENTIDAD_TIMEOUT_SECONDS debe ser mayor que cero.")

    @property
    def jwks_endpoint(self) -> str:
        return self.jwks_url or f"{self.identity_url.rstrip('/')}/.well-known/jwks.json"

    @classmethod
    def from_environment(cls) -> "Config":
        return cls(
            identity_url=_required("IDENTIDAD_URL").rstrip("/"),
            jwt_issuer=_required("JWT_ISSUER"),
            jwt_audience=os.getenv("JWT_AUDIENCE", "solventa-api"),
            jwks_url=os.getenv("JWKS_URL") or None,
            identity_timeout_seconds=_positive_float("IDENTIDAD_TIMEOUT_SECONDS", 5),
        )


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"{name} es obligatoria.")
    return value


def _positive_float(name: str, default: float) -> float:
    value = float(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} debe ser mayor que cero.")
    return value
