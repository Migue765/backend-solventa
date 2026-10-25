import base64
import hashlib
import secrets
from datetime import timedelta
from uuid import UUID, uuid4

from identidad.application.biometric_ports import (
    BiometricCrypto,
    BiometricLimiter,
    BiometricRepository,
)
from identidad.application.ports import Clock, UserRepository
from identidad.application.token_service import TokenService
from identidad.domain.auth import IssuedTokens, User
from identidad.domain.biometric import BiometricChallenge, BiometricCredential, BiometricError


def _b64u(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def signing_message(
    issuer: str,
    purpose: str,
    challenge_id: str,
    credential_id: UUID,
    user_id: UUID,
    public_key: bytes,
    expires_epoch: int,
) -> bytes:
    return "\n".join(
        (
            "SOLVENTA-BIOMETRIC-V1",
            purpose,
            "movil",
            _b64u(hashlib.sha256(issuer.encode("utf-8")).digest()),
            challenge_id,
            str(credential_id),
            str(user_id),
            _b64u(hashlib.sha256(public_key).digest()),
            str(expires_epoch),
        )
    ).encode("utf-8")


class BiometricService:
    def __init__(
        self,
        repository: BiometricRepository,
        users: UserRepository,
        tokens: TokenService,
        crypto: BiometricCrypto,
        limiter: BiometricLimiter,
        clock: Clock,
        issuer: str,
    ) -> None:
        self.repository = repository
        self._users = users
        self._tokens = tokens
        self._crypto = crypto
        self._limiter = limiter
        self._clock = clock
        self._issuer = issuer

    def authenticated_user(self, bearer: str) -> User:
        user = self._users.find_by_id(self._crypto.subject(bearer))
        if user is None:
            raise BiometricError("sesion_invalida", 401)
        self._client(user)
        return user

    def enrollment_challenge(
        self, bearer: str, password: str, encoded_key: str
    ) -> BiometricChallenge:
        user = self.authenticated_user(bearer)
        self._limiter.reserve("enroll:" + str(user.user_id))
        verified = self._tokens.authenticate_password(user.email, password)
        if verified.user_id != user.user_id:
            raise BiometricError()
        public_key = self._crypto.public_key(encoded_key)
        return self._challenge("enrolar", uuid4(), user.user_id, public_key)

    def login_challenge(self, credential_id: UUID) -> BiometricChallenge:
        credential = self._active_credential(credential_id)
        self._user(credential.user_id)
        self._limiter.reserve("challenge:" + str(credential_id))
        return self._challenge("login", credential_id, credential.user_id, credential.public_key)

    def enroll(
        self,
        bearer: str,
        credential_id: UUID,
        challenge_id: str,
        signature: str,
        correlation: str,
    ) -> None:
        user = self.authenticated_user(bearer)
        self._limiter.reserve("enroll-proof:" + str(user.user_id))
        challenge = self._proof(credential_id, challenge_id, signature, "enrolar", user.user_id)
        if not self.repository.enroll(challenge, correlation):
            raise BiometricError()

    def login(
        self, credential_id: UUID, challenge_id: str, signature: str, correlation: str
    ) -> IssuedTokens:
        credential = self._active_credential(credential_id)
        self._limiter.reserve("proof:" + str(credential_id))
        challenge = self._proof(credential_id, challenge_id, signature, "login", credential.user_id)
        issued = self.repository.login(
            challenge,
            lambda: self._tokens.prepare_session(
                self._user(credential.user_id), "movil", credential_id
            ),
            correlation,
        )
        if issued is None:
            raise BiometricError()
        return issued

    def credentials(self, bearer: str) -> tuple[BiometricCredential, ...]:
        return self.repository.credentials(self.authenticated_user(bearer).user_id)

    def revoke(self, bearer: str, credential_id: UUID, correlation: str) -> None:
        user = self.authenticated_user(bearer)
        self.repository.revoke(user.user_id, credential_id, correlation)

    def _user(self, user_id: UUID) -> User:
        user = self._users.find_by_id(user_id)
        if user is None:
            raise BiometricError()
        self._client(user)
        return user

    @staticmethod
    def _client(user: User) -> None:
        if user.role != "cliente":
            raise BiometricError("acceso_denegado", 403)

    def _active_credential(self, credential_id: UUID) -> BiometricCredential:
        credential = self.repository.credential(credential_id)
        if credential is None or credential.revoked_at is not None:
            raise BiometricError()
        return credential

    def _challenge(
        self, purpose: str, credential_id: UUID, user_id: UUID, public_key: bytes
    ) -> BiometricChallenge:
        now = self._clock().replace(microsecond=0)
        expires = now + timedelta(seconds=60)
        challenge_id = secrets.token_urlsafe(32)
        challenge = BiometricChallenge(
            challenge_id,
            credential_id,
            user_id,
            purpose,
            public_key,
            signing_message(
                self._issuer,
                purpose,
                challenge_id,
                credential_id,
                user_id,
                public_key,
                int(expires.timestamp()),
            ),
            now,
            expires,
        )
        self.repository.add_challenge(challenge)
        return challenge

    def _proof(
        self,
        credential_id: UUID,
        challenge_id: str,
        signature: str,
        purpose: str,
        user_id: UUID,
    ) -> BiometricChallenge:
        challenge = self.repository.challenge(challenge_id)
        if (
            challenge is None
            or challenge.consumed
            or challenge.expires_at <= self._clock()
            or challenge.credential_id != credential_id
            or challenge.user_id != user_id
            or challenge.purpose != purpose
        ):
            raise BiometricError()
        if purpose == "login":
            credential = self._active_credential(credential_id)
            if credential.public_key != challenge.public_key:
                raise BiometricError()
        self._crypto.verify(challenge.public_key, challenge.message, signature)
        return challenge
