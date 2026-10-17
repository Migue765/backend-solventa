from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from bff_web.domain.auth import AccessClaims, TokenSet, User


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    correo: EmailStr
    contrasena: str = Field(min_length=8, max_length=128)


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nombre: str = Field(min_length=1, max_length=120)
    correo: EmailStr = Field(max_length=254)
    contrasena: str = Field(min_length=8, max_length=128)


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh_token: str = Field(alias="refreshToken", min_length=1, max_length=2048)


class LogoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh_token: str | None = Field(default=None, alias="refreshToken", max_length=2048)


def user_to_json(user: User) -> dict[str, object]:
    return {
        "id": str(user.user_id),
        "nombre": user.name,
        "correo": user.email,
        "rol": user.role,
    }


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
