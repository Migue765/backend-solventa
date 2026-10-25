import os
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse
from uuid import uuid4

import pytest
from identidad.domain.errors import TooManyAttemptsError
from identidad.infrastructure.biometric_limiter import RedisBiometricLimiter
from redis import Redis


def test_real_redis_lua_reserves_quota_atomically_and_denies_zero_ttl() -> None:
    url = os.environ.get("TEST_REDIS_URL")
    if not url:
        pytest.skip("TEST_REDIS_URL no configurada: limiter Redis real no comprobado")
    parsed = urlparse(url)
    if parsed.hostname not in {"localhost", "127.0.0.1", "redis"} or parsed.path != "/15":
        pytest.fail("Redis de prueba debe ser local/servicio CI en base aislada 15")
    client = Redis.from_url(url, decode_responses=True, socket_timeout=3)
    suffix = "test:" + str(uuid4())
    key = "identidad:biometric:" + suffix
    limiter = RedisBiometricLimiter(client, 5, 300)

    def attempt(_: int) -> bool:
        try:
            limiter.reserve(suffix)
            return True
        except TooManyAttemptsError:
            return False

    try:
        assert client.ping()
        with ThreadPoolExecutor(max_workers=10) as pool:
            assert sum(pool.map(attempt, range(20))) == 5
        client.set(key, "5", px=300)
        assert client.ttl(key) == 0
        with pytest.raises(TooManyAttemptsError) as denied:
            limiter.reserve(suffix)
        assert denied.value.retry_after == 1
    finally:
        client.delete(key)
        client.close()
