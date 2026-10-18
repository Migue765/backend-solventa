import base64
import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from identidad.domain.auth import User
from identidad.infrastructure.jwt_issuer import RsaJwtIssuer

USER = User(uuid4(), "Ana Cliente", "ana@solventa.co", "cliente")


def test_kid_is_the_sha256_of_the_public_key_der(private_key_pem: str) -> None:
    issuer = RsaJwtIssuer(private_key_pem, "https://x", "aud")
    key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
    der = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )

    expected = base64.urlsafe_b64encode(hashlib.sha256(der).digest()).rstrip(b"=").decode()

    assert issuer.kid == expected
    assert issuer.jwks()["keys"][0]["kid"] == expected


def test_jwks_is_unpadded_base64url_and_not_shared(private_key_pem: str) -> None:
    issuer = RsaJwtIssuer(private_key_pem, "https://x", "aud")

    jwk = issuer.jwks()["keys"][0]
    jwk["n"] = "alterado"

    assert issuer.jwks()["keys"][0]["n"] != "alterado"
    assert set(issuer.jwks()["keys"][0]) == {"kty", "use", "alg", "kid", "n", "e"}
    assert "=" not in issuer.jwks()["keys"][0]["n"]
    assert issuer.jwks()["keys"][0]["e"] == "AQAB"


def test_issued_token_header_and_claims(private_key_pem: str) -> None:
    issuer = RsaJwtIssuer(private_key_pem, "https://x", "aud")
    now = datetime.now(UTC)

    token = issuer.issue(USER, "web", now, now + timedelta(hours=1))

    header = jwt.get_unverified_header(token)
    assert header == {"alg": "RS256", "typ": "JWT", "kid": issuer.kid}
    claims = jwt.decode(token, options={"verify_signature": False})
    assert claims["sub"] == str(USER.user_id)
    assert claims["exp"] - claims["iat"] == 3600


def test_every_token_has_a_unique_jti(private_key_pem: str) -> None:
    issuer = RsaJwtIssuer(private_key_pem, "https://x", "aud")
    now = datetime.now(UTC)

    ids = {
        jwt.decode(
            issuer.issue(USER, "web", now, now + timedelta(hours=1)),
            options={"verify_signature": False},
        )["jti"]
        for _ in range(5)
    }

    assert len(ids) == 5


def test_invalid_pem_is_rejected() -> None:
    with pytest.raises(ValueError, match="PEM"):
        RsaJwtIssuer("no es un pem", "https://x", "aud")


def test_non_rsa_key_is_rejected() -> None:
    pem = (
        ec.generate_private_key(ec.SECP256R1())
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        .decode()
    )

    with pytest.raises(ValueError, match="RSA"):
        RsaJwtIssuer(pem, "https://x", "aud")


def test_rsa_key_generated_here_is_not_the_session_key(private_key_pem: str) -> None:
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other_pem = other.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()

    assert (
        RsaJwtIssuer(other_pem, "https://x", "a").kid
        != RsaJwtIssuer(private_key_pem, "https://x", "a").kid
    )
