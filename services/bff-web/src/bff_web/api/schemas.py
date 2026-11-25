import base64
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StrictBool,
    field_validator,
)

from bff_web.domain.auth import AccessClaims, TokenSet, User


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    correo: EmailStr
    contrasena: str = Field(min_length=8, max_length=128)


class EncryptedLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    correo: EmailStr = Field(max_length=254)
    credencialCifrada: str = Field(min_length=1, max_length=8192, strict=True)


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nombre: str = Field(min_length=1, max_length=120)
    correo: EmailStr = Field(max_length=254)
    contrasena: str = Field(min_length=8, max_length=128)


class RegistrationConsent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    autorizado: StrictBool
    versionTerminos: Literal["1.0"]


class EncryptedRegisterRequest(EncryptedLoginRequest):
    nombre: str = Field(min_length=1, max_length=120)
    tipoDocumento: Literal["cc", "ce", "passport", "ppt"]
    numeroDocumento: str = Field(min_length=5, max_length=16)
    telefono: str = Field(pattern=r"^\+57[0-9]{10}$")
    fechaNacimiento: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    consentimiento: RegistrationConsent

    @field_validator("nombre", "correo", "telefono", "numeroDocumento", mode="before")
    @classmethod
    def trim_fields(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class RegistrationPublicKey(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kty: Literal["RSA"]
    n: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    e: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    alg: Literal["RSA-OAEP-256"]
    use: Literal["enc"]
    kid: str = Field(min_length=1)

    @field_validator("e")
    @classmethod
    def validate_exponent(cls, value: str) -> str:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        exponent = int.from_bytes(decoded, "big")
        if exponent < 3 or exponent % 2 == 0:
            raise ValueError("Clave RSA invalida.")
        return value

    @field_validator("n")
    @classmethod
    def validate_modulus(cls, value: str) -> str:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        if int.from_bytes(decoded, "big").bit_length() < 2048:
            raise ValueError("Clave RSA demasiado corta.")
        return value


class RegistrationChallenge(BaseModel):
    model_config = ConfigDict(extra="forbid")
    desafioId: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    clavePublica: RegistrationPublicKey
    expiraEn: AwareDatetime


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh_token: str = Field(alias="refreshToken", min_length=1, max_length=2048)


class LogoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh_token: str | None = Field(default=None, alias="refreshToken", max_length=2048)


def user_to_json(user: User) -> dict[str, object]:
    result: dict[str, object] = {
        "id": str(user.user_id),
        "nombre": user.name,
        "correo": user.email,
        "rol": user.role,
    }
    if user.role in {"asesor", "socio"}:
        result["socios"] = list(user.partner_ids)
    if user.permissions:
        result["permisos"] = list(user.permissions)
    return result


def created_user_to_json(user: User) -> dict[str, object]:
    return {"usuario": user_to_json(user)}


def token_set_to_json(tokens: TokenSet, now: datetime) -> dict[str, object]:
    return {
        "usuario": user_to_json(tokens.user),
        "tokenType": "Bearer",
        "accessToken": tokens.access_token,
        "refreshToken": tokens.refresh_token,
        "expiraEn": isoformat(now + timedelta(seconds=tokens.expires_in)),
        "refreshExpiraEn": isoformat(now + timedelta(seconds=tokens.refresh_expires_in)),
    }


def claims_to_json(claims: AccessClaims) -> dict[str, object]:
    return {"usuario": user_to_json(claims.user), "expiraEn": isoformat(claims.expires_at)}


def isoformat(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
