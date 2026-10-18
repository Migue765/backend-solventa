from collections.abc import Iterator

import pytest
from bff_movil.app import create_app
from bff_movil.config import Config
from bff_movil.infrastructure.jwt_verifier import JwksKeyStore, JwtVerifier
from flask import Flask
from support import (
    AUDIENCE,
    ISSUER,
    NOW,
    FakeIdentity,
    FakeJwks,
    IdentityServer,
    KeyPair,
    make_keypair,
    sign_token,
)


@pytest.fixture(scope="session")
def keypair() -> KeyPair:
    return make_keypair("kid-1")


@pytest.fixture(scope="session")
def other_keypair() -> KeyPair:
    return make_keypair("kid-1")


@pytest.fixture
def jwks(keypair: KeyPair) -> FakeJwks:
    return FakeJwks([keypair])


@pytest.fixture
def identity() -> FakeIdentity:
    return FakeIdentity()


@pytest.fixture
def app(jwks: FakeJwks, identity: FakeIdentity) -> Flask:
    flask_app = create_app(
        Config(identity_url="http://identidad.test", jwt_issuer=ISSUER),
        identity_gateway=identity,
        token_verifier=JwtVerifier(JwksKeyStore(jwks.fetch), ISSUER, AUDIENCE, "movil"),
    )
    flask_app.extensions["clock"] = lambda: NOW
    return flask_app


@pytest.fixture
def token(keypair: KeyPair) -> str:
    return sign_token(keypair)


@pytest.fixture
def identity_server() -> Iterator[IdentityServer]:
    server = IdentityServer()
    yield server
    server.close()
