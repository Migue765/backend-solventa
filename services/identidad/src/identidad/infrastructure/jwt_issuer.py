import base64
import hashlib
from datetime import datetime
from typing import Any
from uuid import uuid4

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

from identidad.domain.auth import User
from identidad.domain.errors import TokenIssuanceError

ALGORITHM = "RS256"


class RsaJwtIssuer:
    def __init__(self, private_key_pem: str, issuer: str, audience: str) -> None:
        try:
            key = serialization.load_pem_private_key(private_key_pem.encode("utf-8"), password=None)
        except (TypeError, ValueError) as error:
            raise ValueError("JWT_PRIVATE_KEY no es un PEM válido.") from error
        if not isinstance(key, RSAPrivateKey):
            raise ValueError("JWT_PRIVATE_KEY debe ser una clave RSA.")

        self._private_key = key
        self._issuer = issuer
        self._audience = audience
        public_key = key.public_key()
        public_der = public_key.public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        self._kid = _b64url(hashlib.sha256(public_der).digest())
        numbers = public_key.public_numbers()
        self._jwk: dict[str, Any] = {
            "kty": "RSA",
            "use": "sig",
            "alg": ALGORITHM,
            "kid": self._kid,
            "n": _b64url(_int_to_bytes(numbers.n)),
            "e": _b64url(_int_to_bytes(numbers.e)),
        }

    @property
    def kid(self) -> str:
        return self._kid

    def jwks(self) -> dict[str, Any]:
        return {"keys": [dict(self._jwk)]}

    def issue(self, user: User, client_id: str, issued_at: datetime, expires_at: datetime) -> str:
        claims = {
            "iss": self._issuer,
            "sub": str(user.user_id),
            "aud": self._audience,
            "iat": int(issued_at.timestamp()),
            "exp": int(expires_at.timestamp()),
            "jti": str(uuid4()),
            "scope": user.role,
            "rol": user.role,
            "nombre": user.name,
            "correo": user.email,
            "client_id": client_id,
        }
        if user.role in {"asesor", "socio"}:
            claims["socios"] = list(user.partner_ids)
        try:
            return jwt.encode(
                claims,
                self._private_key,
                algorithm=ALGORITHM,
                headers={"kid": self._kid},
            )
        except (jwt.PyJWTError, ValueError, TypeError) as error:
            raise TokenIssuanceError() from error


def _int_to_bytes(value: int) -> bytes:
    return value.to_bytes((value.bit_length() + 7) // 8, "big")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")
