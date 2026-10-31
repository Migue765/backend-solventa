from typing import cast

from redis import Redis

from identidad.domain.errors import TooManyAttemptsError

RESERVE = """
local n = redis.call('INCR', KEYS[1])
if n == 1 or redis.call('TTL', KEYS[1]) < 0 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
if n > tonumber(ARGV[2]) then return math.max(1, redis.call('TTL', KEYS[1])) end
return 0
"""


class RedisBiometricLimiter:
    def __init__(self, client: Redis, maximum: int, window_seconds: int) -> None:
        self._client = client
        self._maximum = maximum
        self._window = window_seconds

    def reserve(self, key: str) -> None:
        retry = cast(
            int,
            self._client.eval(
                RESERVE, 1, "identidad:biometric:" + key, str(self._window), str(self._maximum)
            ),
        )
        if retry > 0:
            raise TooManyAttemptsError(retry)
