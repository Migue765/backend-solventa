from collections.abc import Callable
from uuid import UUID, uuid4

from identidad.application.ports import Clock, LoginAttemptLimiter, UserRepository
from identidad.domain.auth import User

CLIENT_ROLE = "cliente"


class RegistrationService:
    def __init__(
        self,
        users: UserRepository,
        attempt_limiter: LoginAttemptLimiter,
        clock: Clock,
        password_hasher: Callable[[str], str],
        id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        self._users = users
        self._attempt_limiter = attempt_limiter
        self._clock = clock
        self._password_hasher = password_hasher
        self._id_factory = id_factory

    def register(
        self,
        name: str,
        email: str,
        password: str,
        document_type: str | None = None,
        identification: str | None = None,
    ) -> User:
        now = self._clock()
        normalized_email = email.strip().casefold()
        attempt_key = f"registro:{normalized_email}"

        self._attempt_limiter.ensure_allowed(attempt_key, now)
        self._attempt_limiter.register_failure(attempt_key, now)

        user = User(
            self._id_factory(),
            name.strip(),
            normalized_email,
            CLIENT_ROLE,
            document_type=document_type,
            identification=identification,
        )
        self._users.add(user, self._password_hasher(password))
        return user
