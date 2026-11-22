import hashlib
import secrets
from collections.abc import Callable
from datetime import timedelta

from identidad.application.ports import (
    Clock,
    LoginAttemptLimiter,
    PasswordRecoveryRepository,
    RecoveryMailSender,
    UserRepository,
)


class RecoveryUnavailableError(Exception):
    pass


class InvalidRecoveryError(Exception):
    pass


class PasswordRecoveryService:
    def __init__(
        self,
        users: UserRepository,
        tokens: PasswordRecoveryRepository,
        sender: RecoveryMailSender | None,
        limiter: LoginAttemptLimiter,
        clock: Clock,
        password_hasher: Callable[[str], str],
    ) -> None:
        self._users = users
        self._tokens = tokens
        self._sender = sender
        self._limiter = limiter
        self._clock = clock
        self._password_hasher = password_hasher

    def request(self, email: str) -> None:
        now = self._clock()
        normalized = email.strip().casefold()
        key = "recovery:email:" + hashlib.sha256(normalized.encode()).hexdigest()
        self._limiter.ensure_allowed(key, now)
        self._limiter.register_failure(key, now)
        # Comprobar el transporte antes de consultar la cuenta evita enumeracion por configuracion.
        if self._sender is None:
            raise RecoveryUnavailableError()
        self._sender.ensure_available()
        credential = self._users.find_credential(normalized)
        if credential is None:
            return
        token = secrets.token_urlsafe(48)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        self._tokens.add(token_hash, credential.user.user_id, now, now + timedelta(minutes=15))
        try:
            self._sender.send(credential.user.email, token)
        except RecoveryUnavailableError:
            self._tokens.discard(token_hash, self._clock())
            raise

    def confirm(self, token: str, password: str) -> None:
        if not 8 <= len(password) <= 128:
            raise ValueError("Contrasena fuera de la politica de registro.")
        password_hash = self._password_hasher(password)
        if not self._tokens.consume(
            hashlib.sha256(token.encode()).hexdigest(), password_hash, self._clock()
        ):
            raise InvalidRecoveryError()
