import json
import os
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import cast
from urllib.parse import urlparse

import pytest
from identidad.infrastructure.encrypted_login import (
    PREFIX,
    EncryptedLogin,
    InvalidEnvelopeError,
)
from jwcrypto import jwe, jwk
from redis import Redis


@pytest.fixture
def real_redis() -> Iterator[Redis]:
    url = os.environ.get("TEST_REDIS_URL")
    if not url:
        pytest.skip("TEST_REDIS_URL no configurada: Redis real no comprobado")
    parsed = urlparse(url)
    if parsed.hostname not in {"localhost", "127.0.0.1", "redis"} or parsed.path != "/15":
        pytest.fail("Redis de prueba debe ser local/servicio CI y usar la base aislada 15")
    client = Redis.from_url(url, decode_responses=True, socket_timeout=2)
    assert client.ping()
    yield client
    client.close()


def test_real_redis_expiry_replay_and_atomic_concurrency(real_redis: Redis) -> None:
    key = jwk.JWK.generate(kty="RSA", size=2048)
    signer = jwk.JWK.generate(kty="RSA", size=2048)
    service = EncryptedLogin(
        key.export_to_pem(private_key=True, password=None).decode(),
        signer.export_to_pem().decode(),
        real_redis,
        lambda: datetime.now(UTC),
    )
    created: list[str] = []

    def issue() -> tuple[str, str]:
        challenge = service.challenge()
        identifier = str(challenge["desafioId"])
        created.append(PREFIX + identifier)
        public = challenge["clavePublica"]
        assert isinstance(public, dict)
        assert 58 <= cast(int, real_redis.ttl(PREFIX + identifier)) <= 60
        envelope = jwe.JWE(
            json.dumps(
                {
                    "desafioId": identifier,
                    "correo": "sintetico@example.com",
                    "contrasena": "sintetica-no-usuario",
                }
            ).encode(),
            protected=json.dumps(
                {
                    "alg": "RSA-OAEP-256",
                    "enc": "A256GCM",
                    "kid": public["kid"],
                    "typ": "solventa-login+jwe",
                }
            ),
        )
        envelope.add_recipient(jwk.JWK(**public))
        return identifier, str(envelope.serialize(compact=True))

    try:
        _, compact = issue()

        def consume(_: int) -> bool:
            try:
                assert service.decrypt_and_consume("sintetico@example.com", compact)
                return True
            except InvalidEnvelopeError:
                return False

        with ThreadPoolExecutor(max_workers=8) as pool:
            assert sum(pool.map(consume, range(8))) == 1
        assert not consume(0)
        identifier, expired = issue()
        assert real_redis.pexpire(PREFIX + identifier, 20)
        deadline = time.monotonic() + 2
        while real_redis.exists(PREFIX + identifier) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not real_redis.exists(PREFIX + identifier)
        with pytest.raises(InvalidEnvelopeError):
            service.decrypt_and_consume("sintetico@example.com", expired)
    finally:
        if created:
            real_redis.delete(*created)
