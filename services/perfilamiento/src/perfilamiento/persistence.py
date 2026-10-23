import hashlib
import hmac
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, String, UniqueConstraint, create_engine, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from perfilamiento.domain import (
    Consentimiento,
    ErrorPerfilamiento,
    EventoConsentimiento,
    Fuente,
    Perfil,
)


class Base(DeclarativeBase):
    pass


class PerfilModel(Base):
    __tablename__ = "perfiles"
    __table_args__ = (UniqueConstraint("cliente_id", "version"),)

    perfil_id: Mapped[UUID] = mapped_column(primary_key=True)
    cliente_id: Mapped[UUID] = mapped_column(index=True)
    version: Mapped[int]
    contenido: Mapped[dict[str, object]] = mapped_column(JSON)
    hash_contenido: Mapped[str] = mapped_column(String(64))


class ReferenciaPerfilModel(Base):
    __tablename__ = "referencias_perfil"

    cliente_id: Mapped[UUID] = mapped_column(primary_key=True)
    referencia_id: Mapped[UUID] = mapped_column(primary_key=True)
    perfil_id: Mapped[UUID] = mapped_column(unique=True)


class VersionPerfilModel(Base):
    __tablename__ = "versiones_perfilamiento"

    cliente_id: Mapped[UUID] = mapped_column(primary_key=True)
    ultima_version: Mapped[int]


class ConsultaModel(Base):
    __tablename__ = "consultas_perfilamiento"

    consulta_id: Mapped[UUID] = mapped_column(primary_key=True)
    contenido: Mapped[dict[str, object]] = mapped_column(JSON)


class FuenteCacheModel(Base):
    __tablename__ = "cache_fuentes"

    cliente_id: Mapped[UUID] = mapped_column(primary_key=True)
    consentimiento_id: Mapped[UUID] = mapped_column(primary_key=True)
    consentimiento_version: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(64), primary_key=True)
    contenido: Mapped[dict[str, object]] = mapped_column(JSON)
    hash_contenido: Mapped[str] = mapped_column(String(64))


class EventoConsentimientoModel(Base):
    __tablename__ = "eventos_consentimiento"
    __table_args__ = (UniqueConstraint("cliente_id", "version"),)

    evento_id: Mapped[UUID] = mapped_column(primary_key=True)
    cliente_id: Mapped[UUID] = mapped_column(index=True)
    version: Mapped[int]
    contenido: Mapped[dict[str, object]] = mapped_column(JSON)
    acuse: Mapped[dict[str, object]] = mapped_column(JSON)


def _hash(contenido: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(contenido, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class RepositorioPerfiles:
    def __init__(self, database_url: str) -> None:
        self.engine = create_engine(database_url)
        self._sesiones = sessionmaker(self.engine)

    def inicializar(self) -> None:
        Base.metadata.create_all(self.engine)

    def _siguiente_version(self, sesion: Session, cliente_id: UUID) -> int:
        insertar = sqlite_insert if self.engine.dialect.name == "sqlite" else pg_insert
        sesion.execute(
            insertar(VersionPerfilModel)
            .values(cliente_id=cliente_id, ultima_version=0)
            .on_conflict_do_nothing()
        )
        historico = (
            select(func.coalesce(func.max(PerfilModel.version), 0))
            .where(PerfilModel.cliente_id == cliente_id)
            .scalar_subquery()
        )
        sesion.execute(
            update(VersionPerfilModel)
            .where(
                VersionPerfilModel.cliente_id == cliente_id,
                VersionPerfilModel.ultima_version < historico,
            )
            .values(ultima_version=historico)
        )
        return sesion.execute(
            update(VersionPerfilModel)
            .where(VersionPerfilModel.cliente_id == cliente_id)
            .values(ultima_version=VersionPerfilModel.ultima_version + 1)
            .returning(VersionPerfilModel.ultima_version)
        ).scalar_one()

    def guardar(self, perfil: Perfil, auditoria: dict[str, object]) -> Perfil:
        try:
            with self._sesiones.begin() as sesion:
                perfil = perfil.model_copy(
                    update={"version": self._siguiente_version(sesion, perfil.cliente_id)}
                )
                contenido = perfil.model_dump(mode="json", by_alias=True)
                sesion.add(
                    PerfilModel(
                        perfil_id=perfil.perfil_id,
                        cliente_id=perfil.cliente_id,
                        version=perfil.version,
                        contenido=contenido,
                        hash_contenido=_hash(contenido),
                    )
                )
                sesion.add(
                    ReferenciaPerfilModel(
                        cliente_id=perfil.cliente_id,
                        referencia_id=perfil.referencia_id,
                        perfil_id=perfil.perfil_id,
                    )
                )
                sesion.add(ConsultaModel(consulta_id=uuid4(), contenido=auditoria))
                for fuente in perfil.fuentes:
                    self._guardar_fuente(
                        sesion,
                        perfil.cliente_id,
                        perfil.consentimiento_id,
                        perfil.consentimiento_version,
                        fuente,
                    )
        except IntegrityError as error:
            raise ErrorPerfilamiento("conflicto_version", 409) from error
        return perfil

    def obtener(self, perfil_id: UUID, cliente_id: UUID) -> Perfil:
        with self._sesiones() as sesion:
            fila = sesion.get(PerfilModel, perfil_id)
            if fila is None or fila.cliente_id != cliente_id:
                raise ErrorPerfilamiento("perfil_no_encontrado", 404)
            if not hmac.compare_digest(_hash(fila.contenido), fila.hash_contenido):
                raise ErrorPerfilamiento("perfil_no_integro", 503)
            return Perfil.model_validate(fila.contenido)

    def buscar_por_referencia(self, cliente_id: UUID, referencia_id: UUID) -> Perfil | None:
        with self._sesiones() as sesion:
            referencia = sesion.get(
                ReferenciaPerfilModel,
                {"cliente_id": cliente_id, "referencia_id": referencia_id},
            )
            fila = sesion.get(PerfilModel, referencia.perfil_id) if referencia is not None else None
            if fila is None:
                return None
            if not hmac.compare_digest(_hash(fila.contenido), fila.hash_contenido):
                raise ErrorPerfilamiento("perfil_no_integro", 503)
            return Perfil.model_validate(fila.contenido)

    def auditar(
        self,
        auditoria: dict[str, object],
        consentimiento: Consentimiento | None = None,
        fuentes: tuple[Fuente, ...] = (),
    ) -> None:
        with self._sesiones.begin() as sesion:
            sesion.add(ConsultaModel(consulta_id=uuid4(), contenido=auditoria))
            if consentimiento is not None:
                for fuente in fuentes:
                    self._guardar_fuente(
                        sesion,
                        consentimiento.cliente_id,
                        consentimiento.consentimiento_id,
                        consentimiento.version,
                        fuente,
                    )

    def _guardar_fuente(
        self,
        sesion: Session,
        cliente_id: UUID,
        consentimiento_id: UUID,
        version: int,
        fuente: Fuente,
    ) -> None:
        contenido = fuente.model_dump(mode="json", by_alias=True)
        insertar = sqlite_insert if self.engine.dialect.name == "sqlite" else pg_insert
        sentencia = insertar(FuenteCacheModel).values(
            cliente_id=cliente_id,
            consentimiento_id=consentimiento_id,
            consentimiento_version=version,
            nombre=fuente.nombre,
            contenido=contenido,
            hash_contenido=_hash(contenido),
        )
        sesion.execute(
            sentencia.on_conflict_do_update(
                index_elements=[
                    "cliente_id",
                    "consentimiento_id",
                    "consentimiento_version",
                    "nombre",
                ],
                set_={"contenido": contenido, "hash_contenido": _hash(contenido)},
            )
        )

    def buscar_fuente(self, consentimiento: Consentimiento, nombre: str) -> Fuente | None:
        with self._sesiones() as sesion:
            fila = sesion.get(
                FuenteCacheModel,
                (
                    consentimiento.cliente_id,
                    consentimiento.consentimiento_id,
                    consentimiento.version,
                    nombre,
                ),
            )
            if fila is None:
                return None
            if not hmac.compare_digest(_hash(fila.contenido), fila.hash_contenido):
                raise ErrorPerfilamiento("fuente_no_integra", 503)
            return Fuente.model_validate(fila.contenido)

    def aplicar_consentimiento(self, evento: EventoConsentimiento) -> dict[str, object]:
        contenido = evento.model_dump(mode="json", by_alias=True)
        with self._sesiones() as sesion:
            existente = sesion.get(EventoConsentimientoModel, evento.evento_id)
            if existente is not None:
                if existente.contenido != contenido:
                    raise ErrorPerfilamiento("conflicto_evento", 409)
                return existente.acuse
        try:
            with self._sesiones.begin() as sesion:
                fila = EventoConsentimientoModel(
                    evento_id=evento.evento_id,
                    cliente_id=evento.cliente_id,
                    version=evento.version,
                    contenido=contenido,
                    acuse={},
                )
                sesion.add(fila)
                sesion.flush()
                version = sesion.scalar(
                    select(func.max(EventoConsentimientoModel.version)).where(
                        EventoConsentimientoModel.cliente_id == evento.cliente_id
                    )
                )
                acuse: dict[str, object] = {
                    "eventoId": str(evento.evento_id),
                    "versionAplicada": version,
                    "aplicadaEn": datetime.now(UTC).isoformat(),
                }
                fila.acuse = acuse
                sesion.add(
                    ConsultaModel(
                        consulta_id=uuid4(),
                        contenido={**contenido, **acuse, "resultado": "evento_aplicado"},
                    )
                )
        except IntegrityError as error:
            with self._sesiones() as sesion:
                existente = sesion.get(EventoConsentimientoModel, evento.evento_id)
                if existente is not None and existente.contenido == contenido:
                    return existente.acuse
            raise ErrorPerfilamiento("conflicto_evento", 409) from error
        return acuse

    def validar_version_consentimiento(self, consentimiento: Consentimiento) -> None:
        with self._sesiones() as sesion:
            ultimo = sesion.scalar(
                select(EventoConsentimientoModel)
                .where(EventoConsentimientoModel.cliente_id == consentimiento.cliente_id)
                .order_by(EventoConsentimientoModel.version.desc())
                .limit(1)
            )
            if ultimo is not None and (
                ultimo.version > consentimiento.version
                or (
                    ultimo.version == consentimiento.version
                    and (
                        ultimo.contenido["estado"] != "autorizado"
                        or ultimo.contenido["consentimientoId"]
                        != str(consentimiento.consentimiento_id)
                    )
                )
            ):
                raise ErrorPerfilamiento("consentimiento_no_vigente", 403)
