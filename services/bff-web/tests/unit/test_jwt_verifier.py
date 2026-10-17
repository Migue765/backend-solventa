import time
from datetime import UTC, datetime
from typing import Any

import pytest
from bff_web.domain.errors import IdentityUnavailableError, InvalidAccessTokenError
from bff_web.infrastructure.jwt_verifier import JwksKeyStore, JwtVerifier
from support import (
    AUDIENCE,
    ISSUER,
    USER,
    FakeJwks,
    KeyPair,
    ManualClock,
    forge_hs256_token,
    forge_unsigned_token,
    make_keypair,
    sign_token,
)


def build(jwks: FakeJwks, clock: ManualClock | None = None, client_id: str = "web") -> JwtVerifier:
    store = JwksKeyStore(jwks.fetch, monotonic=clock or ManualClock())
    return JwtVerifier(store, ISSUER, AUDIENCE, client_id)


def test_valid_token_returns_claims(jwks: FakeJwks, keypair: KeyPair) -> None:
    claims = build(jwks).verify(sign_token(keypair))

    assert claims.user == USER
    assert claims.client_id == "web"
    assert claims.scope == "cotizaciones"
    assert claims.expires_at > datetime.now(UTC)


def test_expiry_allows_ten_seconds_of_leeway(jwks: FakeJwks, keypair: KeyPair) -> None:
    verifier = build(jwks)
    issued = int(time.time())

    verifier.verify(sign_token(keypair, iat=issued - 100, exp=issued - 5))
    with pytest.raises(InvalidAccessTokenError):
        verifier.verify(sign_token(keypair, iat=issued - 100, exp=issued - 30))


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "otra-api"},
        {"iss": "https://intruso.test"},
        {"aud": None},
        {"iss": None},
        {"exp": None},
        {"sub": "no-es-uuid"},
        {"sub": None},
        {"nombre": None},
        {"client_id": "movil"},
        {"client_id": None},
    ],
)
def test_invalid_claims_are_rejected(
    jwks: FakeJwks, keypair: KeyPair, overrides: dict[str, Any]
) -> None:
    with pytest.raises(InvalidAccessTokenError):
        build(jwks).verify(sign_token(keypair, **overrides))


def test_token_signed_by_another_key_is_rejected(jwks: FakeJwks, other_keypair: KeyPair) -> None:
    with pytest.raises(InvalidAccessTokenError):
        build(jwks).verify(sign_token(other_keypair))


def test_unsigned_token_is_rejected(jwks: FakeJwks, keypair: KeyPair) -> None:
    with pytest.raises(InvalidAccessTokenError):
        build(jwks).verify(forge_unsigned_token(keypair.kid))


def test_hs256_signed_with_the_public_key_is_rejected(jwks: FakeJwks, keypair: KeyPair) -> None:
    with pytest.raises(InvalidAccessTokenError):
        build(jwks).verify(forge_hs256_token(keypair.public_pem, keypair.kid))


def test_other_signature_algorithms_are_rejected(jwks: FakeJwks, keypair: KeyPair) -> None:
    with pytest.raises(InvalidAccessTokenError):
        build(jwks).verify(sign_token(keypair, algorithm="RS512"))


@pytest.mark.parametrize("token", ["", "basura", "a.b.c", "a.b"])
def test_malformed_tokens_are_rejected(jwks: FakeJwks, token: str) -> None:
    with pytest.raises(InvalidAccessTokenError):
        build(jwks).verify(token)


def test_token_without_kid_is_rejected(jwks: FakeJwks, keypair: KeyPair) -> None:
    import jwt

    token = jwt.encode(
        {"iss": ISSUER, "aud": AUDIENCE, "sub": str(USER.user_id)},
        keypair.private_pem,
        algorithm="RS256",
    )

    with pytest.raises(InvalidAccessTokenError):
        build(jwks).verify(token)


def test_keys_are_cached_until_the_ttl_expires(jwks: FakeJwks, keypair: KeyPair) -> None:
    clock = ManualClock()
    verifier = build(jwks, clock)

    verifier.verify(sign_token(keypair))
    verifier.verify(sign_token(keypair))
    assert jwks.calls == 1

    clock.advance(601)
    verifier.verify(sign_token(keypair))
    assert jwks.calls == 2


def test_unknown_kid_reloads_the_jwks_once_and_accepts_a_rotated_key(
    jwks: FakeJwks, keypair: KeyPair
) -> None:
    clock = ManualClock()
    verifier = build(jwks, clock)
    verifier.verify(sign_token(keypair))
    rotated = make_keypair("kid-2")
    jwks.keys.append(rotated)
    clock.advance(11)

    claims = verifier.verify(sign_token(rotated))

    assert claims.user == USER
    assert jwks.calls == 2


def test_unknown_kid_reload_is_rate_limited(jwks: FakeJwks, keypair: KeyPair) -> None:
    clock = ManualClock()
    verifier = build(jwks, clock)
    verifier.verify(sign_token(keypair))
    attacker = make_keypair("kid-desconocido")

    for _ in range(5):
        with pytest.raises(InvalidAccessTokenError):
            verifier.verify(sign_token(attacker))

    assert jwks.calls == 1
    clock.advance(11)
    with pytest.raises(InvalidAccessTokenError):
        verifier.verify(sign_token(attacker))
    assert jwks.calls == 2


def test_unreachable_jwks_is_unavailable_not_unauthorized(jwks: FakeJwks, keypair: KeyPair) -> None:
    jwks.failing = True

    with pytest.raises(IdentityUnavailableError):
        build(jwks).verify(sign_token(keypair))


def test_malformed_jwks_document_is_unavailable(keypair: KeyPair) -> None:
    store = JwksKeyStore(lambda: {"keys": "no-es-lista"})
    verifier = JwtVerifier(store, ISSUER, AUDIENCE, "web")

    with pytest.raises(IdentityUnavailableError):
        verifier.verify(sign_token(keypair))


def test_non_rsa_and_foreign_algorithm_keys_are_ignored(keypair: KeyPair) -> None:
    document = {
        "keys": [
            {"kty": "oct", "kid": "kid-1", "k": "c2VjcmV0"},
            {**keypair.jwk(), "kid": "kid-hs", "alg": "HS256"},
            keypair.jwk(),
        ]
    }
    verifier = JwtVerifier(JwksKeyStore(lambda: document), ISSUER, AUDIENCE, "web")

    assert verifier.verify(sign_token(keypair)).user == USER
    with pytest.raises(InvalidAccessTokenError):
        verifier.verify(sign_token(keypair, kid="kid-hs"))


def test_token_for_the_mobile_channel_is_accepted_by_the_mobile_verifier(
    jwks: FakeJwks, keypair: KeyPair
) -> None:
    claims = build(jwks, client_id="movil").verify(sign_token(keypair, client_id="movil"))

    assert claims.client_id == "movil"
