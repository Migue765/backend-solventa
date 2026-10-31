import base64
import json
import re
import secrets
from datetime import UTC, timedelta
from typing import Any, cast

from jwcrypto import jwe, jwk
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from redis import Redis
from redis.exceptions import RedisError

from identidad.application.ports import Clock

TTL_SECONDS = 60
COMPACT_PATTERN = re.compile(r"^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+){4}$")
PREFIX = "identidad:login-challenge:"


class InvalidEnvelopeError(Exception):
    pass


class LoginEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    desafioId: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    correo: EmailStr = Field(max_length=254)
    contrasena: str = Field(min_length=1, max_length=128)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("Duplicate JSON member")
        result[name] = value
    return result


class EncryptedLogin:
    def __init__(self, private_pem: str, signing_pem: str, client: Redis, clock: Clock) -> None:
        try:
            self._key = jwk.JWK.from_pem(private_pem.encode())
            if self._key.get("kty") != "RSA" or not self._key.has_private:
                raise ValueError("Se requiere una clave RSA privada.")
            modulus = cast(str, self._key.get("n"))
            modulus_bytes = base64.urlsafe_b64decode(modulus + "=" * (-len(modulus) % 4))
            if int.from_bytes(modulus_bytes, "big").bit_length() < 2048:
                raise ValueError("La clave RSA debe tener al menos 2048 bits.")
            if self._key.thumbprint() == jwk.JWK.from_pem(signing_pem.encode()).thumbprint():
                raise ValueError("La clave de cifrado debe ser distinta de la clave JWT.")
        except Exception as error:
            raise ValueError(
                "LOGIN_ENCRYPTION_PRIVATE_KEY no es válida o no es independiente."
            ) from error
        self._kid = self._key.thumbprint()
        self._header = {
            "alg": "RSA-OAEP-256",
            "enc": "A256GCM",
            "kid": self._kid,
            "typ": "solventa-login+jwe",
        }
        self._client = client
        self._clock = clock

    def challenge(self) -> dict[str, object]:
        expires = self._clock() + timedelta(seconds=TTL_SECONDS)
        challenge_id = secrets.token_urlsafe(32)
        if not self._client.set(
            PREFIX + challenge_id, expires.timestamp(), ex=TTL_SECONDS, nx=True
        ):
            raise RedisError("No fue posible guardar el desafío.")
        public_key = json.loads(self._key.export_public())
        return {
            "desafioId": challenge_id,
            "clavePublica": {
                "kty": "RSA",
                "n": public_key["n"],
                "e": public_key["e"],
                "alg": "RSA-OAEP-256",
                "use": "enc",
                "kid": self._kid,
            },
            "expiraEn": expires.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        }

    def decrypt_and_consume(self, email: str, compact: str) -> str:
        try:
            if len(compact) > 8192 or not COMPACT_PATTERN.fullmatch(compact):
                raise ValueError("Invalid compact JWE")
            protected = compact.split(".", 1)[0]
            header = json.loads(
                base64.urlsafe_b64decode(protected + "=" * (-len(protected) % 4)),
                object_pairs_hook=_unique_object,
            )
            if header != self._header:
                raise ValueError("Invalid protected header")
            envelope = jwe.JWE(algs=["RSA-OAEP-256", "A256GCM"])
            envelope.deserialize(compact, key=self._key)
            if len(envelope.payload) > 1024:
                raise ValueError("Invalid plaintext length")
            payload = LoginEnvelope.model_validate(
                json.loads(envelope.payload, object_pairs_hook=_unique_object)
            )
            if str(payload.correo) != email:
                raise ValueError("Email mismatch")
        except Exception as error:
            raise InvalidEnvelopeError() from error

        # GETDEL is atomic across workers; never consume before authenticated envelope validation.
        expires = cast(str | None, self._client.getdel(PREFIX + payload.desafioId))
        if expires is None or float(expires) <= self._clock().timestamp():
            raise InvalidEnvelopeError()
        return payload.contrasena
