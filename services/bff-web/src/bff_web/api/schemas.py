from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from bff_web.domain.auth import UserSession


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    correo: EmailStr
    contrasena: str = Field(min_length=8, max_length=128)


def session_to_json(session: UserSession) -> dict[str, object]:
    return {
        "usuario": {
            "id": str(session.user.user_id),
            "nombre": session.user.name,
            "correo": session.user.email,
            "rol": session.user.role,
        },
        "expiraEn": _isoformat(session.expires_at),
    }


def _isoformat(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")
