import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import JSON, String, UniqueConstraint, create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from perfilamiento.domain import (
    Consentimiento,
    ConsultaInformacionCliente,
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


class OperacionPerfilModel(Base):
    __tablename__ = "operaciones_perfil"

    cliente_id: Mapped[UUID] = mapped_column(primary_key=True)
    operation_id: Mapped[UUID] = mapped_column(primary_key=True)
    perfil_id: Mapped[UUID] = mapped_column(unique=True)


class ConsultaModel(Base):
    __tablename__ = "consultas_perfilamiento"

    consulta_id: Mapped[UUID] = mapped_column(primary_key=True)
    contenido: Mapped[dict[str, object]] = mapped_column(JSON)


class ConsultaInformacionModel(Base):
    __tablename__ = "consultas_informacion_cliente"

    socio_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    operation_id: Mapped[UUID] = mapped_column(primary_key=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64))
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

    def siguiente_version(self, cliente_id: UUID) -> int:
        with self._sesiones() as sesion:
            version = sesion.scalar(
                select(func.max(PerfilModel.version)).where(PerfilModel.cliente_id == cliente_id)
            )
            return (version or 0) + 1

    def guardar(self, perfil: Perfil, auditoria: dict[str, object]) -> Perfil:
        contenido = perfil.model_dump(mode="json", by_alias=True)
        try:
            with self._sesiones.begin() as sesion:
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
                sesion.add(
                    OperacionPerfilModel(
                        cliente_id=perfil.cliente_id,
                        operation_id=perfil.operation_id,
                        perfil_id=perfil.perfil_id,
                    )
                )
                sesion.add(ConsultaModel(consulta_id=uuid4(), contenido=auditoria))
            return perfil
        except IntegrityError as error:
            existente = self.buscar_por_operacion(perfil.cliente_id, perfil.operation_id)
            if (
                existente is not None
                and existente.referencia_id == perfil.referencia_id
                and existente.credito_id == perfil.credito_id
                and existente.consentimiento_id == perfil.consentimiento_id
                and existente.consentimiento_version == perfil.consentimiento_version
            ):
                return existente
            raise ErrorPerfilamiento("conflicto_version", 409) from error

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

    def buscar_por_operacion(self, cliente_id: UUID, operation_id: UUID) -> Perfil | None:
        with self._sesiones() as sesion:
            operacion = sesion.get(
                OperacionPerfilModel,
                {"cliente_id": cliente_id, "operation_id": operation_id},
            )
            fila = sesion.get(PerfilModel, operacion.perfil_id) if operacion is not None else None
            if fila is None:
                return None
            if not hmac.compare_digest(_hash(fila.contenido), fila.hash_contenido):
                raise ErrorPerfilamiento("perfil_no_integro", 503)
            return Perfil.model_validate(fila.contenido)

    def fuente_en_cache(self, cliente_id: UUID, nombre: str, ahora: datetime) -> Fuente | None:
        with self._sesiones() as sesion:
            filas = sesion.scalars(
                select(PerfilModel)
                .where(PerfilModel.cliente_id == cliente_id)
                .order_by(PerfilModel.version.desc())
            )
            for fila in filas:
                if not hmac.compare_digest(_hash(fila.contenido), fila.hash_contenido):
                    raise ErrorPerfilamiento("perfil_no_integro", 503)
                perfil = Perfil.model_validate(fila.contenido)
                for fuente in perfil.fuentes:
                    if fuente.nombre == nombre and ahora - fuente.consultada_en <= timedelta(
                        minutes=30
                    ):
                        return fuente
            return None

    def auditar(self, auditoria: dict[str, object]) -> None:
        with self._sesiones.begin() as sesion:
            sesion.add(ConsultaModel(consulta_id=uuid4(), contenido=auditoria))

    def buscar_consulta_informacion(
        self, socio_id: str, operation_id: UUID
    ) -> tuple[ConsultaInformacionCliente, str] | None:
        with self._sesiones() as sesion:
            fila = sesion.get(
                ConsultaInformacionModel,
                {"socio_id": socio_id, "operation_id": operation_id},
            )
            if fila is None:
                return None
            if not hmac.compare_digest(_hash(fila.contenido), fila.hash_contenido):
                raise ErrorPerfilamiento("consulta_no_integra", 503)
            return (
                ConsultaInformacionCliente.model_validate(fila.contenido),
                fila.request_fingerprint,
            )

    def guardar_consulta_informacion(
        self, consulta: ConsultaInformacionCliente, request_fingerprint: str
    ) -> None:
        contenido = consulta.model_dump(mode="json", by_alias=True)
        try:
            with self._sesiones.begin() as sesion:
                sesion.add(
                    ConsultaInformacionModel(
                        socio_id=consulta.socio_id,
                        operation_id=consulta.operation_id,
                        request_fingerprint=request_fingerprint,
                        contenido=contenido,
                        hash_contenido=_hash(contenido),
                    )
                )
        except IntegrityError as error:
            raise ErrorPerfilamiento("conflicto_idempotencia", 409) from error

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
