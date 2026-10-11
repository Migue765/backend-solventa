import math
import threading
from collections import defaultdict, deque
from datetime import datetime, timedelta

from bff_web.domain.errors import TooManyAttemptsError


class InMemoryLoginAttemptLimiter:
    def __init__(self, max_attempts: int, window: timedelta) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts debe ser mayor que cero.")
        if window <= timedelta(0):
            raise ValueError("window debe ser mayor que cero.")

        self._max_attempts = max_attempts
        self._window = window
        self._attempts: dict[str, deque[datetime]] = defaultdict(deque)
        self._lock = threading.Lock()

    def ensure_allowed(self, key: str, now: datetime) -> None:
        with self._lock:
            attempts = self._active_attempts(key, now)
            if len(attempts) >= self._max_attempts:
                retry_at = attempts[0] + self._window
                retry_after = max(1, math.ceil((retry_at - now).total_seconds()))
                raise TooManyAttemptsError(retry_after)

    def register_failure(self, key: str, now: datetime) -> None:
        with self._lock:
            self._active_attempts(key, now).append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._attempts.pop(key, None)

    def _active_attempts(self, key: str, now: datetime) -> deque[datetime]:
        attempts = self._attempts[key]
        threshold = now - self._window
        while attempts and attempts[0] <= threshold:
            attempts.popleft()
        return attempts
