import math
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, sessionmaker

from cotizacion.domain.errors import ConflictoIdempotenciaError
from cotizacion.infrastructure.persistence.models import AdmisionSocioModel, ReservaOperacionModel


class AdmisionRechazada(Exception):
    def __init__(self, codigo: str, status: int, retry_after: int) -> None:
        super().__init__(codigo)
        self.codigo = codigo
        self.status = status
        self.retry_after = retry_after


class AdmisionSocios:
    """Reservas compartidas por replicas; nunca mantiene transacciones durante HTTP."""

    def __init__(
        self,
        sesiones: sessionmaker[Session],
        cuota: int = 60,
        concurrencia: int = 2,
        reloj: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sesiones = sesiones
        self._cuota = cuota
        self._concurrencia = concurrencia
        self._reloj = reloj

    def configurar(self, socio: str, cuota: int, concurrencia: int) -> None:
        if not socio.strip() or len(socio) > 50 or cuota < 1 or concurrencia < 1:
            raise ValueError("Socio, cuota y concurrencia deben ser validos.")
        with self._sesiones.begin() as sesion:
            insert = pg_insert if sesion.get_bind().dialect.name == "postgresql" else sqlite_insert
            sesion.execute(
                insert(AdmisionSocioModel)
                .values(
                    socio_id=socio,
                    ventana=self._reloj(),
                    usadas=0,
                    cuota_por_minuto=cuota,
                    max_concurrentes=concurrencia,
                )
                .on_conflict_do_update(
                    index_elements=["socio_id"],
                    set_={"cuota_por_minuto": cuota, "max_concurrentes": concurrencia},
                )
            )

    def estado(self, socio: str, operation_id: UUID) -> str | None:
        with self._sesiones() as sesion:
            reserva = sesion.get(
                ReservaOperacionModel, {"socio_id": socio, "operation_id": operation_id}
            )
            if reserva is None:
                return None
            if reserva.vence_en is not None and _utc(reserva.vence_en) > self._reloj():
                return "operacion_en_curso"
            return "operacion_reintentable"

    @contextmanager
    def reservar(
        self, socio: str, operation_id: UUID, fingerprint: str, *, global_id: bool = False
    ) -> Iterator[UUID]:
        token = self._adquirir(socio, operation_id, fingerprint, global_id=global_id)
        try:
            yield token
        finally:
            with self._sesiones.begin() as sesion:
                sesion.execute(
                    update(ReservaOperacionModel)
                    .where(
                        ReservaOperacionModel.socio_id == socio,
                        ReservaOperacionModel.operation_id == operation_id,
                        ReservaOperacionModel.token == token,
                    )
                    .values(token=None, vence_en=None)
                )

    def _adquirir(
        self, socio: str, operation_id: UUID, fingerprint: str, *, global_id: bool = False
    ) -> UUID:
        ahora = self._reloj()
        token = uuid4()
        with self._sesiones.begin() as sesion:
            dialect = sesion.get_bind().dialect.name
            if dialect not in {"postgresql", "sqlite"}:
                raise RuntimeError("La admision requiere PostgreSQL o SQLite.")
            insert = pg_insert if dialect == "postgresql" else sqlite_insert
            sesion.execute(
                insert(AdmisionSocioModel)
                .values(socio_id=socio, ventana=ahora, usadas=0)
                .on_conflict_do_nothing(index_elements=["socio_id"])
            )
            # UPDATE serializa solo este socio, tambien cuando la fila acaba de crearse.
            sesion.execute(
                update(AdmisionSocioModel)
                .where(AdmisionSocioModel.socio_id == socio)
                .values(usadas=AdmisionSocioModel.usadas)
            )
            limite = sesion.get(AdmisionSocioModel, socio)
            assert limite is not None
            reserva = sesion.get(
                ReservaOperacionModel, {"socio_id": socio, "operation_id": operation_id}
            )
            if reserva is not None:
                if reserva.fingerprint != fingerprint:
                    raise ConflictoIdempotenciaError()
                if reserva.vence_en is not None and _utc(reserva.vence_en) > ahora:
                    raise AdmisionRechazada("operacion_en_curso", 409, 1)
            activas = sesion.scalar(
                select(func.count())
                .select_from(ReservaOperacionModel)
                .where(
                    ReservaOperacionModel.socio_id == socio,
                    ReservaOperacionModel.vence_en > ahora,
                )
            )
            if (activas or 0) >= (limite.max_concurrentes or self._concurrencia):
                raise AdmisionRechazada("socio_ocupado", 429, 1)
            fin_ventana = _utc(limite.ventana) + timedelta(minutes=1)
            if ahora >= fin_ventana:
                limite.ventana, limite.usadas = ahora, 0
                fin_ventana = ahora + timedelta(minutes=1)
            if limite.usadas >= (limite.cuota_por_minuto or self._cuota):
                raise AdmisionRechazada(
                    "cuota_socio_excedida",
                    429,
                    max(1, math.ceil((fin_ventana - ahora).total_seconds())),
                )
            limite.usadas += 1
            if reserva is None:
                inserted = sesion.scalar(
                    insert(ReservaOperacionModel)
                    .values(
                        socio_id=socio,
                        operation_id=operation_id,
                        operation_global=operation_id if global_id else None,
                        fingerprint=fingerprint,
                    )
                    .on_conflict_do_nothing(index_elements=["operation_global"])
                    .returning(ReservaOperacionModel.operation_id)
                )
                if inserted is None:
                    raise ConflictoIdempotenciaError()
                reserva = sesion.get(
                    ReservaOperacionModel, {"socio_id": socio, "operation_id": operation_id}
                )
                assert reserva is not None
            reserva.token = token
            # Superior al timeout de cada dependencia y al timeout de worker de produccion.
            reserva.vence_en = ahora + timedelta(seconds=60)
        return token


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def validar_reserva(sesion: Session, socio: str, operation_id: UUID, token: UUID) -> None:
    vigente = sesion.scalar(
        update(ReservaOperacionModel)
        .where(
            ReservaOperacionModel.socio_id == socio,
            ReservaOperacionModel.operation_id == operation_id,
            ReservaOperacionModel.token == token,
            ReservaOperacionModel.vence_en > datetime.now(UTC),
        )
        .values(token=token)
        .returning(ReservaOperacionModel.operation_id)
    )
    if vigente is None:
        raise AdmisionRechazada("operacion_en_curso", 409, 1)
