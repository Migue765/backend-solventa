from datetime import datetime
from uuid import UUID

from sqlalchemy import JSON, DateTime, ForeignKey, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class UsuarioModel(Base):
    __tablename__ = "usuarios"

    usuario_id: Mapped[UUID] = mapped_column(primary_key=True)
    correo: Mapped[str] = mapped_column(String(254), unique=True)
    nombre: Mapped[str] = mapped_column(String(120))
    rol: Mapped[str] = mapped_column(String(20))
    password_hash: Mapped[str] = mapped_column(String(255))
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RefreshTokenModel(Base):
    __tablename__ = "refresh_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    usuario_id: Mapped[UUID] = mapped_column(ForeignKey("usuarios.usuario_id"), index=True)
    family_id: Mapped[UUID] = mapped_column(index=True)
    client_id: Mapped[str] = mapped_column(String(20))
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expira_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revocado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reemplazado_por: Mapped[str | None] = mapped_column(String(64))


class ConsentModel(Base):
    __tablename__ = "consentimientos_perfilamiento"

    cliente_id: Mapped[UUID] = mapped_column(ForeignKey("usuarios.usuario_id"), primary_key=True)
    version: Mapped[int] = mapped_column(primary_key=True)
    contenido: Mapped[dict[str, object]] = mapped_column(JSON)


class ConsentEventModel(Base):
    __tablename__ = "outbox_consentimientos"

    evento_id: Mapped[UUID] = mapped_column(primary_key=True)
    tipo: Mapped[str] = mapped_column(String(60))
    contenido: Mapped[dict[str, object]] = mapped_column(JSON)
    publicado: Mapped[bool] = mapped_column(default=False)
