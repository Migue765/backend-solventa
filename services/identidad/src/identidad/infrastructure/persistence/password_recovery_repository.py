from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from identidad.infrastructure.persistence.models import (
    BiometricCredentialModel,
    PasswordRecoveryModel,
    RefreshTokenModel,
    UsuarioModel,
)


class SqlPasswordRecoveryRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def add(self, token_hash: str, user_id: UUID, now: datetime, expires_at: datetime) -> None:
        with self._sessions.begin() as session:
            session.add(
                PasswordRecoveryModel(
                    token_hash=token_hash,
                    usuario_id=user_id,
                    creada_en=now,
                    expira_en=expires_at,
                )
            )

    def discard(self, token_hash: str, now: datetime) -> None:
        with self._sessions.begin() as session:
            session.execute(
                update(PasswordRecoveryModel)
                .where(PasswordRecoveryModel.token_hash == token_hash)
                .values(consumida_en=now)
            )

    def consume(self, token_hash: str, password_hash: str, now: datetime) -> bool:
        with self._sessions.begin() as session:
            user_id = session.scalar(
                select(PasswordRecoveryModel.usuario_id).where(
                    PasswordRecoveryModel.token_hash == token_hash
                )
            )
            if user_id is None:
                return False
            # Serializa recuperaciones de una misma cuenta antes de reclamar el token.
            user = session.scalar(
                select(UsuarioModel).where(UsuarioModel.usuario_id == user_id).with_for_update()
            )
            if user is None:
                return False
            claimed = session.scalar(
                update(PasswordRecoveryModel)
                .where(
                    PasswordRecoveryModel.token_hash == token_hash,
                    PasswordRecoveryModel.consumida_en.is_(None),
                    PasswordRecoveryModel.expira_en > now,
                )
                .values(consumida_en=now)
                .returning(PasswordRecoveryModel.usuario_id)
            )
            if claimed is None:
                return False
            user.password_hash = password_hash
            session.execute(
                update(BiometricCredentialModel)
                .where(
                    BiometricCredentialModel.usuario_id == user_id,
                    BiometricCredentialModel.revocada_en.is_(None),
                )
                .values(revocada_en=now)
            )
            session.execute(
                update(RefreshTokenModel)
                .where(
                    RefreshTokenModel.usuario_id == user_id,
                    RefreshTokenModel.revocado_en.is_(None),
                )
                .values(revocado_en=now)
            )
            session.execute(
                update(PasswordRecoveryModel)
                .where(
                    PasswordRecoveryModel.usuario_id == user_id,
                    PasswordRecoveryModel.consumida_en.is_(None),
                )
                .values(consumida_en=now)
            )
            return True
