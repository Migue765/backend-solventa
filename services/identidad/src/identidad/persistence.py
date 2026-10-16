from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import JSON, String, create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from identidad.domain import Consentimiento


class Base(DeclarativeBase):
    pass


class ConsentimientoModel(Base):
    __tablename__ = "consentimientos_perfilamiento"

    cliente_id: Mapped[UUID] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(primary_key=True)
    contenido: Mapped[dict[str, object]] = mapped_column(JSON)


class EventoConsentimientoModel(Base):
    __tablename__ = "outbox_consentimientos"

    evento_id: Mapped[UUID] = mapped_column(primary_key=True)
    tipo: Mapped[str] = mapped_column(String(60))
    contenido: Mapped[dict[str, object]] = mapped_column(JSON)
    publicado: Mapped[bool] = mapped_column(default=False)


class ConflictoVersionError(Exception):
    pass


class RepositorioConsentimientos:
    def __init__(self, database_url: str) -> None:
        self.engine = create_engine(database_url)
        self._sesiones = sessionmaker(self.engine)

    def inicializar(self) -> None:
        Base.metadata.create_all(self.engine)

    def obtener(self, cliente_id: UUID) -> Consentimiento | None:
        with self._sesiones() as sesion:
            fila = sesion.scalar(
                select(ConsentimientoModel)
                .where(ConsentimientoModel.cliente_id == cliente_id)
                .order_by(ConsentimientoModel.version.desc())
                .limit(1)
            )
            return Consentimiento.model_validate(fila.contenido) if fila else None

    def registrar(
        self,
        cliente_id: UUID,
        autorizado: bool,
        version: int,
        ahora: datetime,
        correlation_id: str,
    ) -> Consentimiento:
        actual = self.obtener(cliente_id)
        if (actual.version if actual else 0) != version:
            raise ConflictoVersionError()
        consentimiento = Consentimiento(
            consentimiento_id=actual.consentimiento_id if actual else uuid4(),
            cliente_id=cliente_id,
            version=version + 1,
            estado="autorizado" if autorizado else "revocado",
            efectiva_en=ahora,
            vigente_hasta=ahora + timedelta(days=30),
        )
        contenido = consentimiento.model_dump(mode="json", by_alias=True)
        try:
            with self._sesiones.begin() as sesion:
                sesion.add(
                    ConsentimientoModel(
                        cliente_id=cliente_id,
                        version=consentimiento.version,
                        contenido=contenido,
                    )
                )
                sesion.add(
                    EventoConsentimientoModel(
                        evento_id=uuid4(),
                        tipo="ConsentimientoAutorizado" if autorizado else "ConsentimientoRevocado",
                        contenido={**contenido, "correlationId": correlation_id},
                    )
                )
        except IntegrityError as error:
            raise ConflictoVersionError() from error
        return consentimiento
