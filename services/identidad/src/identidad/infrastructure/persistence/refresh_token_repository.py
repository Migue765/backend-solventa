from datetime import datetime
from typing import Any, cast

from sqlalchemy import CursorResult, update
from sqlalchemy.orm import Session, sessionmaker

from identidad.domain.auth import NewRefreshToken, RotationOutcome, RotationResult
from identidad.infrastructure.persistence.models import RefreshTokenModel as Token


class SqlRefreshTokenRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def add(self, token: NewRefreshToken) -> None:
        with self._sessions() as session:
            session.add(
                Token(
                    token_hash=token.token_hash,
                    usuario_id=token.user_id,
                    family_id=token.family_id,
                    client_id=token.client_id,
                    creado_en=token.created_at,
                    expira_en=token.expires_at,
                )
            )
            session.commit()

    def rotate(
        self,
        token_hash: str,
        client_id: str,
        new_token_hash: str,
        now: datetime,
        expires_at: datetime,
    ) -> RotationResult:
        with self._sessions() as session:
            # La revocación condicional es atómica: de dos usos simultáneos solo uno la logra.
            claimed = cast(
                "CursorResult[Any]",
                session.execute(
                    update(Token)
                    .where(
                        Token.token_hash == token_hash,
                        Token.client_id == client_id,
                        Token.revocado_en.is_(None),
                        Token.expira_en > now,
                    )
                    .values(revocado_en=now, reemplazado_por=new_token_hash)
                ),
            ).rowcount
            current = session.get(Token, token_hash)

            if claimed == 1 and current is not None:
                session.add(
                    Token(
                        token_hash=new_token_hash,
                        usuario_id=current.usuario_id,
                        family_id=current.family_id,
                        client_id=client_id,
                        creado_en=now,
                        expira_en=expires_at,
                    )
                )
                session.commit()
                return RotationResult(RotationOutcome.ROTATED, current.usuario_id)

            if current is None or current.client_id != client_id:
                session.rollback()
                return RotationResult(RotationOutcome.INVALID)

            if current.revocado_en is not None:
                self._revoke_family(session, current.family_id, now)
                session.commit()
                return RotationResult(RotationOutcome.REUSED)

            session.rollback()
            return RotationResult(RotationOutcome.INVALID)

    def revoke_family(self, token_hash: str, now: datetime) -> None:
        with self._sessions() as session:
            current = session.get(Token, token_hash)
            if current is not None:
                self._revoke_family(session, current.family_id, now)
                session.commit()

    @staticmethod
    def _revoke_family(session: Session, family_id: Any, now: datetime) -> None:
        session.execute(
            update(Token)
            .where(Token.family_id == family_id, Token.revocado_en.is_(None))
            .values(revocado_en=now)
        )
