from datetime import datetime, timedelta
from typing import cast

from redis import Redis

from identidad.domain.errors import TooManyAttemptsError


class RedisLoginAttemptLimiter:
    def __init__(
        self,
        client: Redis,
        max_attempts: int,
        window: timedelta,
        prefix: str = "identidad:attempts:",
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts debe ser mayor que cero.")
        if window <= timedelta(0):
            raise ValueError("window debe ser mayor que cero.")

        self._client = client
        self._max_attempts = max_attempts
        self._window_seconds = max(1, int(window.total_seconds()))
        self._prefix = prefix

    def ensure_allowed(self, key: str, now: datetime) -> None:
        redis_key = self._prefix + key
        if int(cast(str | None, self._client.get(redis_key)) or 0) >= self._max_attempts:
            raise TooManyAttemptsError(max(1, cast(int, self._client.ttl(redis_key))))

    def register_failure(self, key: str, now: datetime) -> None:
        redis_key = self._prefix + key
        attempts = cast(int, self._client.incr(redis_key))
        if attempts == 1 or cast(int, self._client.ttl(redis_key)) < 0:
            self._client.expire(redis_key, self._window_seconds)

    def reset(self, key: str) -> None:
        self._client.delete(self._prefix + key)
