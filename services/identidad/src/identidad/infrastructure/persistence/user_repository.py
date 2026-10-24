from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from identidad.domain.auth import StoredCredential, User
from identidad.domain.errors import EmailAlreadyRegisteredError
from identidad.infrastructure.persistence.models import (
    AsesorSocioModel,
    SocioModel,
    UsuarioModel,
    UsuarioSocioModel,
)


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
            user = self._to_user(session, row)
            return StoredCredential(user=user, password_hash=row.password_hash)

    def find_by_id(self, user_id: UUID) -> User | None:
        with self._sessions() as session:
            row = session.get(UsuarioModel, user_id)
            return self._to_user(session, row) if row is not None else None

    def create_advisor(
        self,
        user: User,
        password_hash: str,
        partner_id: str,
        partner_name: str,
    ) -> None:
        if user.role != "asesor":
            raise ValueError("El usuario administrativo debe tener rol asesor.")
        with self._sessions.begin() as session:
            partner = session.get(SocioModel, partner_id)
            if partner is None:
                session.add(SocioModel(socio_id=partner_id, nombre=partner_name, activo=True))
            session.add(
                UsuarioModel(
                    usuario_id=user.user_id,
                    correo=user.email.casefold(),
                    nombre=user.name,
                    rol=user.role,
                    password_hash=password_hash,
                )
            )
            session.add(AsesorSocioModel(asesor_id=user.user_id, socio_id=partner_id))

    def list_clients(self) -> tuple[User, ...]:
        with self._sessions() as session:
            rows = session.scalars(
                select(UsuarioModel)
                .where(UsuarioModel.rol == "cliente")
                .order_by(UsuarioModel.nombre, UsuarioModel.usuario_id)
            ).all()
            return tuple(self._to_user(session, row) for row in rows)

    def create_partner_user(
        self, user: User, password_hash: str, partner_id: str, partner_name: str
    ) -> None:
        if user.role != "socio" or not partner_id.strip() or len(partner_id) > 50:
            raise ValueError("Se requiere un usuario socio y una organizacion valida.")
        with self._sessions.begin() as session:
            partner = session.get(SocioModel, partner_id)
            if partner is None:
                session.add(SocioModel(socio_id=partner_id, nombre=partner_name, activo=True))
            elif not partner.activo:
                raise ValueError("El socio no esta activo.")
            session.add(
                UsuarioModel(
                    usuario_id=user.user_id,
                    correo=user.email.casefold(),
                    nombre=user.name,
                    rol=user.role,
                    password_hash=password_hash,
                )
            )
            session.flush()
            session.add(UsuarioSocioModel(usuario_id=user.user_id, socio_id=partner_id))

    @staticmethod
    def _to_user(session: Session, row: UsuarioModel) -> User:
        partner_ids: tuple[str, ...] = ()
        if row.rol == "asesor":
            partner_ids = tuple(
                session.scalars(
                    select(AsesorSocioModel.socio_id)
                    .join(SocioModel, SocioModel.socio_id == AsesorSocioModel.socio_id)
                    .where(
                        AsesorSocioModel.asesor_id == row.usuario_id,
                        SocioModel.activo.is_(True),
                    )
                    .order_by(AsesorSocioModel.socio_id)
                ).all()
            )
        elif row.rol == "socio":
            partner_ids = tuple(
                session.scalars(
                    select(UsuarioSocioModel.socio_id)
                    .join(SocioModel, SocioModel.socio_id == UsuarioSocioModel.socio_id)
                    .where(
                        UsuarioSocioModel.usuario_id == row.usuario_id,
                        SocioModel.activo.is_(True),
                    )
                    .order_by(UsuarioSocioModel.socio_id)
                ).all()
            )
        return User(row.usuario_id, row.nombre, row.correo, row.rol, partner_ids)
