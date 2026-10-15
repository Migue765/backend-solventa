from datetime import UTC, datetime, timedelta

import fakeredis
import pytest
from identidad.domain.errors import TooManyAttemptsError
from identidad.infrastructure.redis_attempt_limiter import RedisLoginAttemptLimiter

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)


def test_limiter_blocks_after_max_attempts_and_resets() -> None:
    limiter = RedisLoginAttemptLimiter(
        fakeredis.FakeRedis(decode_responses=True), 2, timedelta(minutes=5)
    )

    limiter.ensure_allowed("k", NOW)
    limiter.register_failure("k", NOW)
    limiter.register_failure("k", NOW)

    with pytest.raises(TooManyAttemptsError) as error:
        limiter.ensure_allowed("k", NOW)
    assert 1 <= error.value.retry_after <= 300

    limiter.reset("k")
    limiter.ensure_allowed("k", NOW)


def test_limiter_keys_are_independent() -> None:
    limiter = RedisLoginAttemptLimiter(
        fakeredis.FakeRedis(decode_responses=True), 1, timedelta(minutes=5)
    )
    limiter.register_failure("login:a@x.co", NOW)

    limiter.ensure_allowed("login:b@x.co", NOW)
    with pytest.raises(TooManyAttemptsError):
        limiter.ensure_allowed("login:a@x.co", NOW)


def test_limiter_rejects_invalid_settings() -> None:
    client = fakeredis.FakeRedis(decode_responses=True)
    with pytest.raises(ValueError):
        RedisLoginAttemptLimiter(client, 0, timedelta(minutes=5))
    with pytest.raises(ValueError):
        RedisLoginAttemptLimiter(client, 1, timedelta(0))
