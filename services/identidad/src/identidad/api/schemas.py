from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from identidad.domain.auth import IssuedTokens, User
from identidad.domain.documents import DocumentType, normalize_document


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nombre: str = Field(min_length=1, max_length=120)
    correo: EmailStr = Field(max_length=254)
    contrasena: str = Field(min_length=8, max_length=128)
    tipo_documento: DocumentType | None = Field(default=None, alias="tipoDocumento")
    numero_documento: str | None = Field(
        default=None, alias="numeroDocumento", min_length=5, max_length=16
    )

    @model_validator(mode="after")
    def validate_document(self) -> Self:
        if (self.tipo_documento is None) != (self.numero_documento is None):
            raise ValueError("Tipo y numero de documento deben enviarse juntos.")
        if self.tipo_documento is not None and self.numero_documento is not None:
            self.tipo_documento, self.numero_documento = normalize_document(
                self.tipo_documento, self.numero_documento
            )
        return self


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


class EncryptedTokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grant_type: Literal["encrypted_password"]
    client_id: Literal["web"]
    username: EmailStr = Field(max_length=254)
    credencialCifrada: str = Field(min_length=1, max_length=8192, strict=True)


def user_to_json(user: User) -> dict[str, object]:
    result: dict[str, object] = {
        "id": str(user.user_id),
        "nombre": user.name,
        "correo": user.email,
        "rol": user.role,
    }
    if user.role in {"asesor", "socio"}:
        result["socios"] = list(user.partner_ids)
    if user.document_type is not None and user.identification is not None:
        result["tipoDocumento"] = user.document_type
        result["numeroDocumento"] = user.identification
    return result


def user_created_to_json(user: User) -> dict[str, object]:
    return {"usuario": user_to_json(user)}


def tokens_to_json(issued: IssuedTokens) -> dict[str, object]:
    return {
        "access_token": issued.access_token,
        "token_type": "Bearer",
        "expires_in": issued.expires_in,
        "refresh_token": issued.refresh_token,
        "refresh_expires_in": issued.refresh_expires_in,
        "scope": issued.user.role,
        "usuario": user_to_json(issued.user),
    }
