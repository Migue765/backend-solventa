from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from identidad.domain.auth import StoredCredential, User
from identidad.domain.errors import EmailAlreadyRegisteredError
from identidad.infrastructure.persistence.models import UsuarioModel


class SqlUserRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def add(self, user: User, password_hash: str) -> None:
        with self._sessions() as session:
            session.add(
                UsuarioModel(
                    usuario_id=user.user_id,
                    correo=user.email.casefold(),
                    nombre=user.name,
                    rol=user.role,
                    password_hash=password_hash,
                )
            )
            try:
                session.commit()
            except IntegrityError as error:
                session.rollback()
                raise EmailAlreadyRegisteredError() from error

    def find_credential(self, email: str) -> StoredCredential | None:
        with self._sessions() as session:
            row = session.scalar(
                select(UsuarioModel).where(UsuarioModel.correo == email.casefold())
            )
        if row is None:
            return None
        return StoredCredential(
            user=User(row.usuario_id, row.nombre, row.correo, row.rol),
            password_hash=row.password_hash,
        )

    def find_by_id(self, user_id: UUID) -> User | None:
        with self._sessions() as session:
            row = session.get(UsuarioModel, user_id)
        if row is None:
            return None
        return User(row.usuario_id, row.nombre, row.correo, row.rol)
