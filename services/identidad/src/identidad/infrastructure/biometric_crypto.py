import base64
import re
from uuid import UUID

import jwt
from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

from identidad.domain.biometric import BiometricError


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def decode_b64u(encoded: str, maximum: int) -> bytes:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", encoded) or len(encoded) > maximum * 2:
        raise BiometricError("solicitud_invalida", 400)
    try:
        raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    except ValueError as error:
        raise BiometricError("solicitud_invalida", 400) from error
    if len(raw) > maximum or b64u(raw) != encoded:
        raise BiometricError("solicitud_invalida", 400)
    return raw


class P256BiometricCrypto:
    def __init__(self, private_pem: str, issuer: str, audience: str) -> None:
        session_key = serialization.load_pem_private_key(private_pem.encode(), password=None)
        if not isinstance(session_key, RSAPrivateKey):
            raise ValueError("JWT_PRIVATE_KEY debe ser RSA.")
        self._session_key = session_key.public_key()
        self._issuer = issuer
        self._audience = audience

    def subject(self, bearer: str) -> UUID:
        try:
            claims = jwt.decode(
                bearer,
                self._session_key,
                algorithms=["RS256"],
                issuer=self._issuer,
                audience=self._audience,
                leeway=10,
                options={"require": ["iss", "aud", "exp", "iat", "sub", "client_id"]},
            )
            if claims["client_id"] != "movil":
                raise ValueError("Canal invalido")
            return UUID(claims["sub"])
        except (jwt.PyJWTError, ValueError, TypeError) as error:
            raise BiometricError("sesion_invalida", 401) from error

    def public_key(self, encoded: str) -> bytes:
        raw = decode_b64u(encoded, 91)
        try:
            key = serialization.load_der_public_key(raw)
            if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(
                key.curve, ec.SECP256R1
            ):
                raise ValueError("P-256 requerida")
            canonical = key.public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
            )
            if len(raw) != 91 or canonical != raw:
                raise ValueError("SPKI no canonico")
        except (ValueError, TypeError, UnsupportedAlgorithm) as error:
            raise BiometricError("solicitud_invalida", 400) from error
        return raw

    def verify(self, public_key: bytes, message: bytes, signature: str) -> None:
        raw = decode_b64u(signature, 72)
        try:
            r, s = utils.decode_dss_signature(raw)
            if utils.encode_dss_signature(r, s) != raw:
                raise ValueError("Firma no canonica")
        except ValueError as error:
            raise BiometricError("solicitud_invalida", 400) from error
        key = serialization.load_der_public_key(public_key)
        if not isinstance(key, ec.EllipticCurvePublicKey):
            raise BiometricError()
        try:
            key.verify(raw, message, ec.ECDSA(hashes.SHA256()))
        except InvalidSignature as error:
            raise BiometricError() from error
