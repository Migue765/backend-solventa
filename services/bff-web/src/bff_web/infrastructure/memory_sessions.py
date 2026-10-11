import hashlib
import secrets
import threading
from datetime import datetime

from bff_web.domain.auth import UserSession


class InMemorySessionRepository:
    def __init__(self) -> None:
        self._sessions: dict[str, UserSession] = {}
        self._lock = threading.Lock()

    def create(self, session: UserSession) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions[self._token_key(token)] = session
        return token

    def get(self, token: str, now: datetime) -> UserSession | None:
        token_key = self._token_key(token)
        with self._lock:
            session = self._sessions.get(token_key)
            if session is not None and session.expires_at <= now:
                self._sessions.pop(token_key, None)
                return None
            return session

    def delete(self, token: str) -> None:
        with self._lock:
            self._sessions.pop(self._token_key(token), None)

    @staticmethod
    def _token_key(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()
