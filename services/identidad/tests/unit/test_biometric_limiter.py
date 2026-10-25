from concurrent.futures import ThreadPoolExecutor

import fakeredis
import pytest
from identidad.domain.errors import TooManyAttemptsError
from identidad.infrastructure.biometric_limiter import RedisBiometricLimiter


def test_zero_ttl_does_not_allow_an_excess_attempt() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    redis.set("identidad:biometric:proof:test", "5", px=450)
    assert redis.ttl("identidad:biometric:proof:test") == 0
    with pytest.raises(TooManyAttemptsError) as failure:
        RedisBiometricLimiter(redis, 5, 300).reserve("proof:test")
    assert failure.value.retry_after == 1


def test_reservation_is_atomic_under_concurrency() -> None:
    limiter = RedisBiometricLimiter(fakeredis.FakeRedis(decode_responses=True), 5, 300)

    def reserve(_: int) -> bool:
        try:
            limiter.reserve("proof:test")
            return True
        except TooManyAttemptsError:
            return False

    with ThreadPoolExecutor(max_workers=10) as pool:
        assert sum(pool.map(reserve, range(20))) == 5
