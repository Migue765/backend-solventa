from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from identidad.domain.auth import IssuedTokens, User


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nombre: str = Field(min_length=1, max_length=120)
    correo: EmailStr = Field(max_length=254)
    contrasena: str = Field(min_length=8, max_length=128)


class TokenRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    grant_type: Literal["password", "refresh_token"]
    client_id: Literal["web", "movil"]
    username: EmailStr | None = Field(default=None, max_length=254)
    password: str | None = Field(default=None, min_length=1, max_length=128)
    refresh_token: str | None = Field(default=None, min_length=1, max_length=512)

    @model_validator(mode="after")
    def require_fields_for_grant(self) -> Self:
        if self.grant_type == "password" and (self.username is None or self.password is None):
            raise ValueError("password requiere username y password")
        if self.grant_type == "refresh_token" and self.refresh_token is None:
            raise ValueError("refresh_token requiere refresh_token")
        return self


class RevokeRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    refresh_token: str = Field(min_length=1, max_length=512)


def user_to_json(user: User) -> dict[str, object]:
    return {
        "id": str(user.user_id),
        "nombre": user.name,
        "correo": user.email,
        "rol": user.role,
    }


def user_created_to_json(user: User) -> dict[str, object]:
    return {"usuario": user_to_json(user)}


def tokens_to_json(issued: IssuedTokens) -> dict[str, object]:
    return {
        "access_token": issued.access_token,
        "token_type": "Bearer",
        "expires_in": issued.expires_in,
        "refresh_token": issued.refresh_token,
        "refresh_expires_in": issued.refresh_expires_in,
        "scope": "cliente",
        "usuario": user_to_json(issued.user),
    }
