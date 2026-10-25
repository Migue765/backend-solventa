import base64
import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import CursorResult, delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from identidad.application.ports import Clock
from identidad.domain.auth import IssuedTokens, NewRefreshToken
from identidad.domain.biometric import BiometricChallenge, BiometricCredential, BiometricError
from identidad.infrastructure.persistence.models import (
    BiometricAuditModel as Audit,
)
from identidad.infrastructure.persistence.models import (
    BiometricChallengeModel as Challenge,
)
from identidad.infrastructure.persistence.models import (
    BiometricCredentialModel as Credential,
)
from identidad.infrastructure.persistence.models import (
    RefreshTokenModel as Token,
)
from identidad.infrastructure.persistence.models import UsuarioModel
from identidad.infrastructure.persistence.refresh_token_repository import refresh_model


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _credential(row: Credential) -> BiometricCredential:
    return BiometricCredential(
        row.credencial_id,
        row.usuario_id,
        row.clave_publica,
        row.creada_en.replace(tzinfo=UTC),
        _utc(row.ultimo_uso_en),
        _utc(row.revocada_en),
    )


class SqlBiometricRepository:
    def __init__(self, sessions: sessionmaker[Session], clock: Clock) -> None:
        self._sessions = sessions
        self._clock = clock

    def add_challenge(self, challenge: BiometricChallenge) -> None:
        with self._sessions.begin() as session:
            expired = (
                select(Challenge.desafio_id)
                .where(Challenge.expira_en <= challenge.created_at)
                .limit(100)
            )
            session.execute(delete(Challenge).where(Challenge.desafio_id.in_(expired)))
            session.add(
                Challenge(
                    desafio_id=challenge.challenge_id,
                    credencial_id=challenge.credential_id,
                    usuario_id=challenge.user_id,
                    proposito=challenge.purpose,
                    clave_publica=challenge.public_key,
                    mensaje=challenge.message,
                    creada_en=challenge.created_at,
                    expira_en=challenge.expires_at,
                )
            )

    def challenge(self, challenge_id: str) -> BiometricChallenge | None:
        with self._sessions() as session:
            row = session.get(Challenge, challenge_id)
            if row is None:
                return None
            return BiometricChallenge(
                row.desafio_id,
                row.credencial_id,
                row.usuario_id,
                row.proposito,
                row.clave_publica,
                row.mensaje,
                row.creada_en.replace(tzinfo=UTC),
                row.expira_en.replace(tzinfo=UTC),
                row.consumida_en is not None,
            )

    def credential(self, credential_id: UUID) -> BiometricCredential | None:
        with self._sessions() as session:
            row = session.get(Credential, credential_id)
            return _credential(row) if row is not None else None

    def _lock_challenge(self, session: Session, challenge: BiometricChallenge) -> datetime:
        session.execute(
            update(Challenge)
            .where(Challenge.desafio_id == challenge.challenge_id)
            .values(mensaje=Challenge.mensaje)
        )
        # Read wall time after the UPDATE has acquired its lock, not transaction-start time.
        return self._clock()

    @staticmethod
    def _consume(session: Session, challenge: BiometricChallenge, now: datetime) -> bool:
        result = session.execute(
            update(Challenge)
            .where(
                Challenge.desafio_id == challenge.challenge_id,
                Challenge.credencial_id == challenge.credential_id,
                Challenge.usuario_id == challenge.user_id,
                Challenge.proposito == challenge.purpose,
                Challenge.consumida_en.is_(None),
                Challenge.expira_en > now,
            )
            .values(consumida_en=now)
            .execution_options(synchronize_session=False)
        )
        return cast(CursorResult[Any], result).rowcount == 1

    def enroll(self, challenge: BiometricChallenge, correlation: str) -> bool:
        key_hash = (
            base64.urlsafe_b64encode(hashlib.sha256(challenge.public_key).digest())
            .rstrip(b"=")
            .decode("ascii")
        )
        try:
            with self._sessions.begin() as session:
                now = self._lock_challenge(session, challenge)
                if (
                    session.scalar(
                        select(UsuarioModel.rol).where(UsuarioModel.usuario_id == challenge.user_id)
                    )
                    != "cliente"
                ):
                    raise BiometricError("acceso_denegado", 403)
                row = session.get(Challenge, challenge.challenge_id)
                if row is None or row.consumida_en is not None or challenge.expires_at <= now:
                    return False
                credential = Credential(
                    credencial_id=challenge.credential_id,
                    usuario_id=challenge.user_id,
                    clave_publica=challenge.public_key,
                    clave_hash=key_hash,
                    creada_en=now,
                )
                session.add(credential)
                session.flush()
                # A concurrent insert of the same key can also wait on a unique constraint.
                now = self._clock()
                if not self._consume(session, challenge, now):
                    raise BiometricError()
                credential.creada_en = now
                self._audit(
                    session,
                    "enrolar",
                    "permitido",
                    now,
                    correlation,
                    challenge.user_id,
                    challenge.credential_id,
                )
        except IntegrityError as error:
            with self._sessions() as session:
                duplicate = session.scalar(
                    select(Credential).where(Credential.clave_hash == key_hash)
                )
                if duplicate is not None:
                    raise BiometricError("credencial_ya_registrada", 409) from error
            raise
        return True

    def login(
        self,
        challenge: BiometricChallenge,
        issue: Callable[[], tuple[NewRefreshToken, IssuedTokens]],
        correlation: str,
    ) -> IssuedTokens | None:
        with self._sessions() as session:
            # Lock credential before challenge/token, also when racing revocation.
            active = session.execute(
                update(Credential)
                .where(
                    Credential.credencial_id == challenge.credential_id,
                    Credential.usuario_id == challenge.user_id,
                    Credential.clave_publica == challenge.public_key,
                    Credential.revocada_en.is_(None),
                )
                .values(ultimo_uso_en=Credential.ultimo_uso_en)
            )
            now = self._lock_challenge(session, challenge)
            if cast(CursorResult[Any], active).rowcount != 1 or not self._consume(
                session, challenge, now
            ):
                session.rollback()
                return None
            session.execute(
                update(Credential)
                .where(Credential.credencial_id == challenge.credential_id)
                .values(ultimo_uso_en=now)
            )
            token, issued = issue()
            session.add(refresh_model(token))
            self._audit(
                session,
                "login",
                "permitido",
                now,
                correlation,
                challenge.user_id,
                challenge.credential_id,
            )
            session.commit()
            return issued

    def credentials(self, user_id: UUID) -> tuple[BiometricCredential, ...]:
        with self._sessions() as session:
            rows = session.scalars(
                select(Credential)
                .where(Credential.usuario_id == user_id)
                .order_by(Credential.creada_en, Credential.credencial_id)
            )
            return tuple(_credential(row) for row in rows)

    def revoke(self, user_id: UUID, credential_id: UUID, correlation: str) -> None:
        with self._sessions.begin() as session:
            found = session.execute(
                update(Credential)
                .where(
                    Credential.credencial_id == credential_id,
                    Credential.usuario_id == user_id,
                )
                .values(revocada_en=Credential.revocada_en)
            )
            now = self._clock()
            if cast(CursorResult[Any], found).rowcount == 1:
                session.execute(
                    update(Credential)
                    .where(Credential.credencial_id == credential_id)
                    .values(revocada_en=now)
                )
                session.execute(
                    update(Token)
                    .where(
                        Token.credencial_biometrica_id == credential_id,
                        Token.revocado_en.is_(None),
                    )
                    .values(revocado_en=now)
                )
                session.execute(
                    update(Challenge)
                    .where(
                        Challenge.credencial_id == credential_id,
                        Challenge.consumida_en.is_(None),
                    )
                    .values(consumida_en=now)
                )
            self._audit(session, "revocar", "permitido", now, correlation, user_id, None)

    def audit(
        self,
        operation: str,
        result: str,
        now: datetime,
        correlation: str,
        user_id: UUID | None = None,
        credential_id: UUID | None = None,
    ) -> None:
        with self._sessions.begin() as session:
            self._audit(session, operation, result, now, correlation, user_id, credential_id)

    @staticmethod
    def _audit(
        session: Session,
        operation: str,
        result: str,
        now: datetime,
        correlation: str,
        user_id: UUID | None,
        credential_id: UUID | None,
    ) -> None:
        session.add(
            Audit(
                evento_id=uuid4(),
                creada_en=now,
                correlation_id=correlation,
                operacion=operation,
                resultado=result,
                usuario_id=user_id,
                credencial_id=credential_id,
            )
        )
