import hashlib
import secrets
from collections.abc import Callable
from datetime import timedelta
from uuid import UUID, uuid4

from identidad.application.ports import (
    AccessTokenIssuer,
    Clock,
    LoginAttemptLimiter,
    RefreshTokenRepository,
    UserRepository,
)
from identidad.domain.auth import IssuedTokens, NewRefreshToken, RotationOutcome, User
from identidad.domain.errors import InvalidCredentialsError, InvalidRefreshTokenError
from identidad.infrastructure.passwords import DEFAULT_ITERATIONS, hash_password, verify_password


class TokenService:
    def __init__(
        self,
        users: UserRepository,
        refresh_tokens: RefreshTokenRepository,
        access_tokens: AccessTokenIssuer,
        attempt_limiter: LoginAttemptLimiter,
        clock: Clock,
        access_ttl: timedelta,
        refresh_ttl: Callable[[str], timedelta],
        password_iterations: int | None = None,
        id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        self._users = users
        self._refresh_tokens = refresh_tokens
        self._access_tokens = access_tokens
        self._attempt_limiter = attempt_limiter
        self._clock = clock
        self._access_ttl = access_ttl
        self._refresh_ttl = refresh_ttl
        self._id_factory = id_factory
        iterations = DEFAULT_ITERATIONS if password_iterations is None else password_iterations
        self._dummy_hash = hash_password("not-a-real-password", iterations=iterations)

    def password_grant(self, email: str, password: str, client_id: str) -> IssuedTokens:
        user = self.authenticate_password(email, password)
        stored, issued = self.prepare_session(user, client_id)
        self._refresh_tokens.add(stored)
        return issued

    def authenticate_password(self, email: str, password: str) -> User:
        now = self._clock()
        normalized_email = email.strip().casefold()
        attempt_key = f"login:{normalized_email}"
        self._attempt_limiter.ensure_allowed(attempt_key, now)

        credential = self._users.find_credential(normalized_email)
        encoded_hash = credential.password_hash if credential else self._dummy_hash
        password_matches = verify_password(password, encoded_hash)
        if credential is None or not password_matches:
            self._attempt_limiter.register_failure(attempt_key, now)
            raise InvalidCredentialsError()

        self._attempt_limiter.reset(attempt_key)
        return credential.user

    def prepare_session(
        self, user: User, client_id: str, biometric_credential_id: UUID | None = None
    ) -> tuple[NewRefreshToken, IssuedTokens]:
        now = self._clock()
        refresh_token = _new_refresh_token()
        ttl = self._refresh_ttl(client_id)
        return (
            NewRefreshToken(
                token_hash=_hash(refresh_token),
                user_id=user.user_id,
                family_id=self._id_factory(),
                client_id=client_id,
                created_at=now,
                expires_at=now + ttl,
                biometric_credential_id=biometric_credential_id,
            ),
            self._issued(user, client_id, refresh_token, ttl),
        )

    def refresh_grant(self, refresh_token: str, client_id: str) -> IssuedTokens:
        now = self._clock()
        ttl = self._refresh_ttl(client_id)
        new_refresh_token = _new_refresh_token()
        result = self._refresh_tokens.rotate(
            _hash(refresh_token),
            client_id,
            _hash(new_refresh_token),
            now,
            now + ttl,
        )
        if result.outcome is not RotationOutcome.ROTATED or result.user_id is None:
            raise InvalidRefreshTokenError()

        user = self._users.find_by_id(result.user_id)
        if user is None:
            raise InvalidRefreshTokenError()
        return self._issued(user, client_id, new_refresh_token, ttl)

    def revoke(self, refresh_token: str) -> None:
        self._refresh_tokens.revoke_family(_hash(refresh_token), self._clock())

    def _issued(
        self, user: User, client_id: str, refresh_token: str, refresh_ttl: timedelta
    ) -> IssuedTokens:
        now = self._clock()
        access_token = self._access_tokens.issue(user, client_id, now, now + self._access_ttl)
        return IssuedTokens(
            access_token=access_token,
            expires_in=int(self._access_ttl.total_seconds()),
            refresh_token=refresh_token,
            refresh_expires_in=int(refresh_ttl.total_seconds()),
            user=user,
        )


def _new_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
