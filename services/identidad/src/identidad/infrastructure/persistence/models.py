from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    LargeBinary,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class UsuarioModel(Base):
    __tablename__ = "usuarios"
    __table_args__ = (
        CheckConstraint(
            "(tipo_documento IS NULL AND numero_documento IS NULL) OR "
            "(tipo_documento IS NOT NULL AND numero_documento IS NOT NULL)",
            name="ck_usuarios_documento_completo",
        ),
        UniqueConstraint("tipo_documento", "numero_documento", name="uq_usuarios_documento"),
    )

    usuario_id: Mapped[UUID] = mapped_column(primary_key=True)
    correo: Mapped[str] = mapped_column(String(254), unique=True)
    nombre: Mapped[str] = mapped_column(String(120))
    rol: Mapped[str] = mapped_column(String(20))
    password_hash: Mapped[str] = mapped_column(String(255))
    tipo_documento: Mapped[str | None] = mapped_column(String(16))
    numero_documento: Mapped[str | None] = mapped_column(String(16))
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SocioModel(Base):
    __tablename__ = "socios"

    socio_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    nombre: Mapped[str] = mapped_column(String(120))
    activo: Mapped[bool] = mapped_column(default=True, server_default="1")


class AsesorSocioModel(Base):
    __tablename__ = "asesores_socios"

    asesor_id: Mapped[UUID] = mapped_column(
        ForeignKey("usuarios.usuario_id", ondelete="CASCADE"), primary_key=True
    )
    socio_id: Mapped[str] = mapped_column(
        ForeignKey("socios.socio_id", ondelete="CASCADE"), primary_key=True
    )


class UsuarioSocioModel(Base):
    __tablename__ = "usuarios_socios"

    usuario_id: Mapped[UUID] = mapped_column(
        ForeignKey("usuarios.usuario_id", ondelete="CASCADE"), primary_key=True
    )
    socio_id: Mapped[str] = mapped_column(
        ForeignKey("socios.socio_id", ondelete="CASCADE"), primary_key=True
    )


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
    credencial_biometrica_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("credenciales_biometricas.credencial_id"), index=True
    )


class BiometricCredentialModel(Base):
    __tablename__ = "credenciales_biometricas"

    credencial_id: Mapped[UUID] = mapped_column(primary_key=True)
    usuario_id: Mapped[UUID] = mapped_column(ForeignKey("usuarios.usuario_id"), index=True)
    clave_publica: Mapped[bytes] = mapped_column(LargeBinary(91))
    clave_hash: Mapped[str] = mapped_column(String(43), unique=True)
    creada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ultimo_uso_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revocada_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BiometricChallengeModel(Base):
    __tablename__ = "desafios_biometricos"

    desafio_id: Mapped[str] = mapped_column(String(43), primary_key=True)
    credencial_id: Mapped[UUID] = mapped_column(index=True)
    usuario_id: Mapped[UUID] = mapped_column(ForeignKey("usuarios.usuario_id"))
    proposito: Mapped[str] = mapped_column(String(10))
    clave_publica: Mapped[bytes] = mapped_column(LargeBinary(91))
    mensaje: Mapped[bytes] = mapped_column(LargeBinary(512))
    creada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expira_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    consumida_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BiometricAuditModel(Base):
    __tablename__ = "auditoria_biometrica"

    evento_id: Mapped[UUID] = mapped_column(primary_key=True)
    creada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    correlation_id: Mapped[str] = mapped_column(String(36))
    operacion: Mapped[str] = mapped_column(String(20))
    resultado: Mapped[str] = mapped_column(String(40))
    usuario_id: Mapped[UUID | None] = mapped_column()
    credencial_id: Mapped[UUID | None] = mapped_column()


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
    intentos: Mapped[int] = mapped_column(default=0, server_default="0")
    ultimo_error: Mapped[str | None] = mapped_column(String(80))
    acuse: Mapped[dict[str, object] | None] = mapped_column(JSON)
