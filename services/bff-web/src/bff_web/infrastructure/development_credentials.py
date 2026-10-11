from dataclasses import dataclass

from bff_web.domain.auth import User
from bff_web.domain.errors import InvalidCredentialsError
from bff_web.infrastructure.passwords import hash_password, verify_password


@dataclass(frozen=True)
class DevelopmentCredential:
    user: User
    password_hash: str


class DevelopmentCredentialProvider:
    def __init__(self, credentials: tuple[DevelopmentCredential, ...]) -> None:
        self._credentials = {item.user.email.casefold(): item for item in credentials}
        self._dummy_hash = hash_password("not-a-real-password")

    def authenticate(self, email: str, password: str) -> User:
        credential = self._credentials.get(email.casefold())
        encoded_hash = credential.password_hash if credential else self._dummy_hash
        password_matches = verify_password(password, encoded_hash)

        if credential is None or not password_matches:
            raise InvalidCredentialsError()

        return credential.user
